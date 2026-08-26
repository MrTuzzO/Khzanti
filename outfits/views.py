import asyncio
import copy
import json
import logging
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
import calendar
from django.db import connection, transaction
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
import requests
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from datetime import date as date_type, timedelta

from accounts.models import User
from core.exceptions import ServiceError
from core.pagination import StandardPagination
from wardrobe_items_ai.services import _friendly_error_message, verify_webhook_signature
from .models import DailyOutfitSelection, JobStatus, OutfitJob, OutfitRating, SavedOutfit, TriggerType
from .serializers import (
    DailyOutfitSelectionCreateSerializer,
    DailyOutfitSelectionSerializer,
    OutfitJobSerializer,
    OutfitJobStatusSerializer,
    OutfitRatingSerializer,
    PublicSavedOutfitSerializer,
    RATING_CATEGORIES,
    SavedOutfitCreateSerializer,
    SavedOutfitRatingDetailSerializer,
    SavedOutfitSerializer,
    SavedOutfitUpdateSerializer,
    TodayOutfitSerializer,
    TryOnCreateSerializer,
)
from .services import get_or_create_today_auto_job, save_try_on_to_cloudinary, submit_try_on_job

logger = logging.getLogger(__name__)


class AsyncAPIView(APIView):
    """
    DRF APIView subclass supporting async request handlers natively.
    """
    async def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            self.initial(request, *args, **kwargs)

            if request.method.lower() in self.http_method_names:
                handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed

            if asyncio.iscoroutinefunction(handler):
                response = await handler(request, *args, **kwargs)
            else:
                response = handler(request, *args, **kwargs)

        except Exception as exc:
            response = self.handle_exception(exc)

        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response


class TryOnCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TryOnCreateSerializer
        return OutfitJobSerializer

    def get_queryset(self):
        return (
            OutfitJob.objects.filter(user=self.request.user)
            .select_related("avatar")
            .prefetch_related("wardrobe_items")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        avatar = serializer.validated_data["avatar"]
        items = serializer.validated_data["wardrobe_items"]

        from .services import generate_outfit_reasoning
        reasoning_dict = generate_outfit_reasoning(request.user, items)

        # Create OutfitJob
        job = OutfitJob.objects.create(
            user=request.user,
            avatar=avatar,
            status=JobStatus.PENDING,
            trigger_type=TriggerType.MANUAL,
            reasoning_title=reasoning_dict.get("title", ""),
            reasoning_subtitle=reasoning_dict.get("subtitle", ""),
            reasoning_items=reasoning_dict.get("reasons", []),
            reasoning_note=reasoning_dict.get("style_note", ""),
        )
        job.wardrobe_items.set(items)

        # Trigger AI submission
        submit_try_on_job(job)
        job.refresh_from_db()

        if job.status == JobStatus.FAILED:
            raise ServiceError(
                detail=job.error_message or "Try-on generation failed. Please try again later.",
                debug_detail=job.internal_error_detail,
                status_code=502,
            )

        response_data = {
            "status": "success",
            "code": status.HTTP_201_CREATED,
            "message": "Try-on generation started.",
            "data": {
                "id": job.id,
                "status": job.status,
                "avatar": avatar.id,
                "wardrobe_items": [item.id for item in items],
                "result_image": job.result_image.url if job.result_image else None,
            },
        }
        return Response(response_data, status=status.HTTP_201_CREATED)


class TryOnStatusView(generics.RetrieveAPIView):
    """
    DB-ONLY status check endpoint for OutfitJob.
    Does NOT invoke fal_client, requests, or Cloudinary downloads.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = OutfitJobStatusSerializer

    def get_queryset(self):
        return OutfitJob.objects.filter(user=self.request.user)

    def get(self, request, pk=None, *args, **kwargs):
        try:
            job = self.get_queryset().get(pk=pk)
        except OutfitJob.DoesNotExist:
            raise Http404("Outfit job not found.")

        result_image_url = None
        if job.status == JobStatus.DONE:
            result_image_url = job.display_result_image or None

        return Response({
            "status": job.status,
            "result_image": result_image_url,
            "error_message": job.error_message if job.status == JobStatus.FAILED else "",
        }, status=status.HTTP_200_OK)


class TodayOutfitView(generics.RetrieveAPIView):
    """
    Dashboard-triggered lazy daily AUTO OutfitJob generation & retrieval endpoint.
    GET /api/v1/outfits/today/
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TodayOutfitSerializer

    def get(self, request, *args, **kwargs):
        job = get_or_create_today_auto_job(request.user)

        msg = "Today's outfit is ready." if job.status == JobStatus.DONE else "Today's outfit generation started."
        if job.status == JobStatus.FAILED:
            msg = "Today's outfit generation failed."

        serializer = self.get_serializer(job)
        return Response({
            "status": "success",
            "code": status.HTTP_200_OK,
            "message": msg,
            "data": serializer.data,
        }, status=status.HTTP_200_OK)


class TodayOutfitResetView(APIView):
    """
    Development/testing endpoint to reset today's AUTO OutfitJob for the authenticated user.
    Does NOT invoke any AI services.
    Enabled ONLY when DEBUG=True.
    DELETE /api/v1/outfits/today/reset/
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={
            200: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
        },
        description="Development/testing endpoint to reset today's AUTO OutfitJob for the authenticated user. Enabled ONLY when DEBUG=True."
    )
    def delete(self, request, *args, **kwargs):
        if not getattr(settings, "DEBUG", False):
            return Response(
                {"detail": "This development endpoint is disabled in production."},
                status=status.HTTP_403_FORBIDDEN,
            )

        today_date = timezone.now().date()
        deleted_count, _ = OutfitJob.objects.filter(
            user=request.user,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
        ).delete()

        if deleted_count == 0:
            logger.info(
                "[AUTO OUTFIT RESET] No AUTO outfit job found for user %s on date %s to reset.",
                request.user.id,
                today_date,
            )
            return Response({
                "status": "error",
                "code": status.HTTP_404_NOT_FOUND,
                "message": "No AUTO outfit job found for today to reset.",
                "data": None,
            }, status=status.HTTP_404_NOT_FOUND)

        logger.info(
            "[AUTO OUTFIT RESET] Reset today's AUTO outfit job for user %s (deleted %d job(s))",
            request.user.id,
            deleted_count,
        )

        return Response({
            "status": "success",
            "code": status.HTTP_200_OK,
            "message": "Today's AUTO outfit reset successfully.",
            "data": None,
        }, status=status.HTTP_200_OK)


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class TryOnWebhookView(AsyncAPIView):
    authentication_classes = []
    permission_classes = []

    async def post(self, request, *args, **kwargs):
        raw_body = request.body
        req_id = request.headers.get("X-Fal-Webhook-Request-Id") or request.headers.get("X-Fal-Request-Id") or ""
        user_id = request.headers.get("X-Fal-Webhook-User-Id") or ""
        timestamp = request.headers.get("X-Fal-Webhook-Timestamp") or ""
        sig_hex = request.headers.get("X-Fal-Webhook-Signature") or ""

        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            data = {}

        payload_req_id = data.get("request_id") or req_id

        logger.info(
            "[OUTFITS WEBHOOK TRY-ON] Received webhook: payload_req_id=%s req_id=%s sig_present=%s status=%s",
            payload_req_id,
            req_id,
            bool(sig_hex),
            data.get("status"),
        )

        if sig_hex:
            is_valid = await sync_to_async(verify_webhook_signature)(
                req_id, user_id, timestamp, sig_hex, raw_body
            )
            if not is_valid:
                logger.warning("[OUTFITS WEBHOOK TRY-ON] Signature verification failed for request_id=%s", payload_req_id)
                return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_403_FORBIDDEN)

        if not payload_req_id:
            logger.warning("[OUTFITS WEBHOOK TRY-ON] Missing request_id in payload and headers")
            return Response({"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            job = await OutfitJob.objects.aget(fal_request_id=payload_req_id)
        except OutfitJob.DoesNotExist:
            logger.warning("[OUTFITS WEBHOOK TRY-ON] OutfitJob not found for fal_request_id=%s", payload_req_id)
            return Response({"detail": "Matching processing job not found."}, status=status.HTTP_400_BAD_REQUEST)

        # Idempotency check
        if job.status in (JobStatus.DONE, JobStatus.FAILED):
            logger.info("[OUTFITS WEBHOOK TRY-ON] OutfitJob %s already in terminal state (%s). Skipping duplicate callback.", job.id, job.status)
            return Response({"status": "already_processed", "job_status": job.status}, status=status.HTTP_200_OK)

        status_str = data.get("status")
        payload_body = data.get("payload") if "payload" in data else data

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "Virtual try-on generation failed on fal.ai"
            logger.error("[OUTFITS WEBHOOK TRY-ON] fal.ai returned error for OutfitJob %s: %s", job.id, err_msg)
            job.status = JobStatus.FAILED
            job.internal_error_detail = str(err_msg)
            job.error_message = _friendly_error_message(str(err_msg))
            await job.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        image_url = None
        if isinstance(payload_body, dict):
            if payload_body.get("image") and isinstance(payload_body["image"], dict):
                image_url = payload_body["image"].get("url")
            elif payload_body.get("image") and isinstance(payload_body["image"], str):
                image_url = payload_body["image"]
            elif payload_body.get("images") and isinstance(payload_body["images"], list) and len(payload_body["images"]) > 0:
                first_img = payload_body["images"][0]
                image_url = first_img.get("url") if isinstance(first_img, dict) else first_img

        if not image_url:
            logger.error("[OUTFITS WEBHOOK TRY-ON] No image URL extracted from payload for OutfitJob %s: %s", job.id, payload_body)
            job.status = JobStatus.FAILED
            job.internal_error_detail = f"No image URL returned in payload: {payload_body}"
            job.error_message = "Failed to generate try-on image."
            await job.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        job.fal_cdn_url = image_url
        job.status = JobStatus.DONE
        job.is_saved = False
        job.error_message = ""
        job.internal_error_detail = ""
        await job.asave(update_fields=["fal_cdn_url", "status", "is_saved", "error_message", "internal_error_detail", "updated_at"])
        logger.info("[OUTFITS WEBHOOK TRY-ON] Successfully completed OutfitJob %s", job.id)

        return Response({"status": "ok"}, status=status.HTTP_200_OK)


class TryOnSaveView(APIView):
    """
    Explicitly save a completed try-on result (MANUAL or AUTO) to Cloudinary.
    POST /api/v1/outfits/try-on/<id>/save/
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: OutfitJobSerializer})
    def post(self, request, pk=None, *args, **kwargs):
        job = get_object_or_404(OutfitJob, pk=pk, user=request.user)

        if job.status == JobStatus.FAILED:
            raise ServiceError(
                detail=job.error_message or "Try-on generation failed. Cannot save failed try-on.",
                debug_detail=job.internal_error_detail,
                status_code=400,
            )

        if job.status != JobStatus.DONE:
            raise ServiceError(
                detail="Try-on generation is not completed yet.",
                status_code=400,
            )

        saved_job = save_try_on_to_cloudinary(job)
        serializer = OutfitJobSerializer(saved_job, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


def _parse_month_date_range(query_params):
    """
    Calculates start_date and end_date for filtering based on user-supplied query parameters:
    - start_date / end_date (e.g. 2026-08-01, 2026-08-31)
    - date (e.g. 2026-08 or 2026-08-15)
    - month & year (e.g. month=8&year=2026)
    Defaults to current month if no parameters supplied.
    Handles varying month lengths and leap years.
    """
    today = timezone.now().date()
    year = None
    month = None

    start_date_param = query_params.get("start_date")
    end_date_param = query_params.get("end_date")
    if start_date_param and end_date_param:
        try:
            s_date = date_type.fromisoformat(start_date_param)
            e_date = date_type.fromisoformat(end_date_param)
            return s_date, e_date
        except ValueError:
            pass

    date_param = query_params.get("date")
    if date_param:
        parts = date_param.strip().split("-")
        if len(parts) >= 2:
            try:
                year = int(parts[0])
                month = int(parts[1])
            except ValueError:
                pass

    month_param = query_params.get("month")
    year_param = query_params.get("year")

    if month_param and month is None:
        try:
            month = int(month_param)
        except ValueError:
            pass

    if year_param and year is None:
        try:
            year = int(year_param)
        except ValueError:
            pass

    if year is None:
        year = today.year
    if month is None:
        month = today.month

    if month < 1 or month > 12:
        month = today.month

    _, last_day = calendar.monthrange(year, month)
    start_date = date_type(year, month, 1)
    end_date = date_type(year, month, last_day)
    return start_date, end_date


class SavedOutfitListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SavedOutfitCreateSerializer
        return SavedOutfitSerializer

    def get_queryset(self):
        qs = (
            SavedOutfit.objects.filter(user=self.request.user)
            .select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
        )

        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        month = self.request.query_params.get("month")
        year = self.request.query_params.get("year")
        date_param = self.request.query_params.get("date")

        if start_date or end_date or month or year or date_param:
            s_date, e_date = _parse_month_date_range(self.request.query_params)
            qs = qs.filter(date__range=(s_date, e_date))

        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        read_serializer = SavedOutfitSerializer(
            SavedOutfit.objects.select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
            .get(pk=serializer.instance.pk),
            context=self.get_serializer_context(),
        )
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)


class SavedOutfitDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return SavedOutfitUpdateSerializer
        return SavedOutfitSerializer

    def get_queryset(self):
        return (
            SavedOutfit.objects.filter(user=self.request.user)
            .select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        read_serializer = SavedOutfitSerializer(
            SavedOutfit.objects.select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
            .get(pk=instance.pk),
            context=self.get_serializer_context(),
        )
        return Response(read_serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {"detail": "Saved outfit deleted successfully."},
            status=status.HTTP_200_OK,
        )


def with_rating_aggregates(queryset):
    """
    Annotate a SavedOutfit queryset with ratings_count and per-category averages
    (avg_<category>) in one query, so listing shared outfits stays N+1-free.
    Public-facing only — never exposes who rated what (see
    SavedOutfitRatingsListView for the owner-only per-rater breakdown).
    """
    annotations = {"ratings_count": Count("ratings", distinct=True)}
    for field in RATING_CATEGORIES:
        annotations[f"avg_{field}"] = Avg(f"ratings__{field}")
    return queryset.annotate(**annotations)


class PublicSavedOutfitListView(generics.ListAPIView):
    """
    GET /api/v1/outfits/public/<username>/ - a user's saved outfits that they've
    marked as shared (is_shared=True), for display on their public profile.
    Shows only the aggregate rating (count/average/breakdown) — not who rated it.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PublicSavedOutfitSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        get_object_or_404(User, username=self.kwargs["username"], is_active=True)
        qs = (
            SavedOutfit.objects.filter(user__username=self.kwargs["username"], is_shared=True)
            .select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
            .order_by("-date")
        )
        return with_rating_aggregates(qs)


class RateSavedOutfitView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_ratable_outfit(self, request, pk):
        outfit = get_object_or_404(SavedOutfit, pk=pk, is_shared=True)
        if outfit.user_id == request.user.id:
            raise ValidationError({"detail": "You cannot rate your own outfit."})
        return outfit

    @extend_schema(request=OutfitRatingSerializer, responses={200: OutfitRatingSerializer})
    def post(self, request, pk):
        outfit = self._get_ratable_outfit(request, pk)

        serializer = OutfitRatingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        rating, created = OutfitRating.objects.update_or_create(
            saved_outfit=outfit,
            rater=request.user,
            defaults=serializer.validated_data,
        )

        return Response(
            OutfitRatingSerializer(rating).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, pk):
        outfit = self._get_ratable_outfit(request, pk)
        OutfitRating.objects.filter(saved_outfit=outfit, rater=request.user).delete()
        return Response({"detail": "Rating removed."}, status=status.HTTP_200_OK)


class SavedOutfitRatingsListView(generics.ListAPIView):
    """
    GET /api/v1/outfits/saved/<pk>/ratings/ - who rated this outfit and what they
    gave. Owner-only: strangers only ever see the aggregate via the public
    profile endpoint, never individual raters' identities.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SavedOutfitRatingDetailSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        get_object_or_404(SavedOutfit, pk=self.kwargs["pk"], user=self.request.user)
        return (
            OutfitRating.objects.filter(saved_outfit_id=self.kwargs["pk"])
            .select_related("rater")
            .order_by("-created_at")
        )


class OneMonthOutfitHistoryView(generics.ListAPIView):
    """
    GET /api/v1/outfits/1-months/
    Returns the authenticated user's selected daily outfits (DailyOutfitSelection) for the requested month and year (or date/start_date/end_date).
    Defaults to the current month if no parameters are supplied.
    For each calendar day, returns only the latest saved/selected outfit for that day.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = OutfitJobSerializer

    def get_queryset(self):
        start_date, end_date = _parse_month_date_range(self.request.query_params)
        qs = (
            DailyOutfitSelection.objects.filter(
                user=self.request.user,
                date__range=(start_date, end_date),
            )
            .select_related("outfit_job", "outfit_job__avatar")
            .prefetch_related("outfit_job__wardrobe_items")
            .order_by("date", "-created_at", "-id")
        )

        if connection.vendor == "postgresql":
            return qs.distinct("date")

        seen_dates = set()
        unique_selections = []
        for sel in qs:
            if sel.date not in seen_dates:
                seen_dates.add(sel.date)
                unique_selections.append(sel)
        return unique_selections

    def list(self, request, *args, **kwargs):
        selections = self.get_queryset()
        outfit_jobs = []
        for sel in selections:
            if sel.outfit_job:
                job = copy.copy(sel.outfit_job)
                job.scheduled_date = sel.date
                outfit_jobs.append(job)

        serializer = self.get_serializer(outfit_jobs, many=True)
        return Response(serializer.data)






class DailyOutfitSelectionView(APIView):
    """
    POST /api/v1/outfits/daily-selection/
    Select or replace an outfit for a specific date.

    GET /api/v1/outfits/daily-selection/?date=YYYY-MM-DD
    Get the selected outfit for a specific date.

    DELETE /api/v1/outfits/daily-selection/?date=YYYY-MM-DD
    Delete the selected outfit for a specific date.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=DailyOutfitSelectionCreateSerializer,
        responses={200: DailyOutfitSelectionSerializer, 201: DailyOutfitSelectionSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = DailyOutfitSelectionCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        selected_date = serializer.validated_data["date"]
        job = serializer.validated_data["outfit_job"]

        with transaction.atomic():
            selection, created = DailyOutfitSelection.objects.update_or_create(
                user=request.user,
                date=selected_date,
                defaults={"outfit_job": job},
            )

        read_serializer = DailyOutfitSelectionSerializer(selection, context={"request": request})
        res_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(read_serializer.data, status=res_status)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="date",
                description="Calendar date in YYYY-MM-DD format",
                required=True,
                type=OpenApiTypes.DATE,
            )
        ],
        responses={200: DailyOutfitSelectionSerializer},
    )
    def get(self, request, *args, **kwargs):
        date_param = request.query_params.get("date")
        if not date_param:
            raise ValidationError({"date": ["date query parameter is required."]})

        try:
            target_date = date_type.fromisoformat(date_param.strip())
        except (ValueError, TypeError):
            raise ValidationError({"date": ["Invalid date format. Use YYYY-MM-DD."]})

        try:
            selection = DailyOutfitSelection.objects.select_related("outfit_job").get(
                user=request.user,
                date=target_date,
            )
        except DailyOutfitSelection.DoesNotExist:
            raise Http404("Daily outfit selection for this date not found.")

        serializer = DailyOutfitSelectionSerializer(selection, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="date",
                description="Calendar date in YYYY-MM-DD format",
                required=True,
                type=OpenApiTypes.DATE,
            )
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def delete(self, request, *args, **kwargs):
        date_param = request.query_params.get("date")
        if not date_param:
            raise ValidationError({"date": ["date query parameter is required."]})

        try:
            target_date = date_type.fromisoformat(date_param.strip())
        except (ValueError, TypeError):
            raise ValidationError({"date": ["Invalid date format. Use YYYY-MM-DD."]})

        try:
            selection = DailyOutfitSelection.objects.get(
                user=request.user,
                date=target_date,
            )
        except DailyOutfitSelection.DoesNotExist:
            raise Http404("Daily outfit selection for this date not found.")

        selection.delete()
        return Response(
            {"detail": "Daily outfit selection deleted successfully."},
            status=status.HTTP_200_OK,
        )


