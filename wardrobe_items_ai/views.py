import asyncio
import json
import logging
from asgiref.sync import async_to_sync, sync_to_async
from django.core.files.base import ContentFile
from django.http import Http404
from drf_spectacular.utils import OpenApiTypes, extend_schema
import requests
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ServiceError
from .models import ItemAnalysis, WardrobeItem
from .serializers import (
    ItemAnalysisSerializer,
    ItemAnalysisTriggerSerializer,
    WardrobeItemCreateSerializer,
    WardrobeItemSerializer,
)
from .services import (
    _friendly_error_message,
    _parse_json_response,
    submit_bg_removal_job_async,
    submit_vision_job_async,
    sync_analysis_status,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)


class AsyncAPIView(APIView):
    """
    DRF APIView subclass that natively supports async handlers (async def post, etc.)
    by making dispatch itself an async method and awaiting the handler coroutine.
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


def _raise_if_analysis_failed(analysis: ItemAnalysis) -> None:
    if analysis.status == ItemAnalysis.JobStatus.FAILED:
        is_category_mismatch = "category mismatch" in (analysis.internal_error_detail or "").lower()
        status_code = 422 if is_category_mismatch else 502
        raise ServiceError(
            detail=analysis.error_message or "Failed to analyze wardrobe item. Please try again later.",
            debug_detail=analysis.internal_error_detail,
            status_code=status_code,
        )


class WardrobeItemListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return WardrobeItemCreateSerializer
        return WardrobeItemSerializer

    def get_queryset(self):
        qs = WardrobeItem.objects.filter(user=self.request.user)

        category_param = self.request.query_params.get("category")
        season_param = self.request.query_params.get("season")
        occasion_param = self.request.query_params.get("occasion")

        if category_param:
            if category_param.isdigit():
                qs = qs.filter(category_id=int(category_param))
            else:
                qs = qs.filter(category__name__iexact=category_param) | qs.filter(category__slug__iexact=category_param)

        if season_param:
            qs = qs.filter(season__iexact=season_param)

        if occasion_param:
            qs = qs.filter(occasion__iexact=occasion_param)

        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        item = serializer.instance
        analysis, _ = ItemAnalysis.objects.get_or_create(wardrobe_item=item)
        async_to_sync(submit_bg_removal_job_async)(analysis)

        fresh_item = WardrobeItem.objects.select_related("category", "analysis").get(pk=item.pk)

        headers = self.get_success_headers(serializer.data)
        read_serializer = WardrobeItemSerializer(fresh_item, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class WardrobeItemDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WardrobeItemSerializer

    def get_queryset(self):
        return WardrobeItem.objects.filter(user=self.request.user)


class ItemAnalysisTriggerView(AsyncAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ItemAnalysisTriggerSerializer,
        responses={
            200: ItemAnalysisSerializer,
            201: ItemAnalysisSerializer,
        },
    )
    async def post(self, request, *args, **kwargs):
        input_serializer = ItemAnalysisTriggerSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                input_serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        wardrobe_item_id = input_serializer.validated_data["wardrobe_item_id"]

        try:
            item = await WardrobeItem.objects.select_related("user").aget(pk=wardrobe_item_id)
        except (WardrobeItem.DoesNotExist, ValueError):
            raise Http404("Wardrobe item not found.")

        # Ownership check: must belong to request.user
        if item.user_id != request.user.id:
            raise Http404("Wardrobe item not found.")

        analysis, created = await ItemAnalysis.objects.aget_or_create(wardrobe_item=item)

        if not created:
            # Skip reprocessing if already DONE or currently PROCESSING
            if analysis.status in (ItemAnalysis.JobStatus.DONE, ItemAnalysis.JobStatus.PROCESSING):
                serializer = ItemAnalysisSerializer(analysis)
                return Response(serializer.data, status=status.HTTP_200_OK)

            # If analysis already exists and failed, reset status for retry
            if analysis.status == ItemAnalysis.JobStatus.FAILED:
                analysis.status = ItemAnalysis.JobStatus.PENDING
                analysis.error_message = ""
                analysis.internal_error_detail = ""
                await analysis.asave(update_fields=["status", "error_message", "internal_error_detail", "updated_at"])

        await submit_bg_removal_job_async(analysis)
        _raise_if_analysis_failed(analysis)

        serializer = ItemAnalysisSerializer(analysis)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ItemAnalysisStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ItemAnalysisSerializer

    def get_queryset(self):
        return ItemAnalysis.objects.filter(wardrobe_item__user=self.request.user)

    def get_object(self):
        analysis = super().get_object()
        synced = sync_analysis_status(analysis)
        _raise_if_analysis_failed(synced)
        return synced


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class BgRemovalWebhookView(AsyncAPIView):
    permission_classes = []

    async def post(self, request, *args, **kwargs):
        raw_body = request.body
        req_id = request.headers.get("X-Fal-Webhook-Request-Id") or ""
        user_id = request.headers.get("X-Fal-Webhook-User-Id") or ""
        timestamp = request.headers.get("X-Fal-Webhook-Timestamp") or ""
        sig_hex = request.headers.get("X-Fal-Webhook-Signature") or ""

        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            data = {}

        payload_req_id = data.get("request_id") or req_id

        # Signature verification if signature header is provided
        if sig_hex:
            is_valid = await sync_to_async(verify_webhook_signature)(
                req_id, user_id, timestamp, sig_hex, raw_body
            )
            if not is_valid:
                return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_403_FORBIDDEN)

        if not payload_req_id:
            return Response({"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            analysis = await ItemAnalysis.objects.select_related("wardrobe_item").aget(
                fal_request_id_bg_removal=payload_req_id,
                status=ItemAnalysis.JobStatus.PROCESSING,
            )
        except ItemAnalysis.DoesNotExist:
            return Response({"detail": "Matching processing job not found."}, status=status.HTTP_400_BAD_REQUEST)

        status_str = data.get("status")
        payload_body = data.get("payload") or {}

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "Background removal failed on fal.ai"
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = err_msg
            analysis.error_message = _friendly_error_message(err_msg)
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
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
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = f"No image URL returned in payload: {payload_body}"
            analysis.error_message = "Failed to remove image background."
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        analysis.fal_cdn_url = image_url
        await analysis.asave(update_fields=["fal_cdn_url", "updated_at"])

        await submit_vision_job_async(analysis)
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class VisionWebhookView(AsyncAPIView):
    permission_classes = []

    async def post(self, request, *args, **kwargs):
        raw_body = request.body
        req_id = request.headers.get("X-Fal-Webhook-Request-Id") or ""
        user_id = request.headers.get("X-Fal-Webhook-User-Id") or ""
        timestamp = request.headers.get("X-Fal-Webhook-Timestamp") or ""
        sig_hex = request.headers.get("X-Fal-Webhook-Signature") or ""

        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            data = {}

        payload_req_id = data.get("request_id") or req_id

        if sig_hex:
            is_valid = await sync_to_async(verify_webhook_signature)(
                req_id, user_id, timestamp, sig_hex, raw_body
            )
            if not is_valid:
                return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_403_FORBIDDEN)

        if not payload_req_id:
            return Response({"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            analysis = await ItemAnalysis.objects.select_related("wardrobe_item", "wardrobe_item__category").aget(
                fal_request_id_vision=payload_req_id,
                status=ItemAnalysis.JobStatus.PROCESSING,
            )
        except ItemAnalysis.DoesNotExist:
            return Response({"detail": "Matching processing job not found."}, status=status.HTTP_400_BAD_REQUEST)

        status_str = data.get("status")
        payload_body = data.get("payload") or {}

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "Vision analysis failed on fal.ai"
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = err_msg
            analysis.error_message = _friendly_error_message(err_msg)
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        raw_output = ""
        if isinstance(payload_body, dict):
            raw_output = payload_body.get("output") or payload_body.get("text") or payload_body.get("content") or str(payload_body)
        else:
            raw_output = str(payload_body)

        parsed = _parse_json_response(raw_output)
        category_obj = getattr(analysis.wardrobe_item, "category", None)
        category_name = getattr(category_obj, "name", "clothing item") if category_obj else "clothing item"

        matches_category = parsed.get("matches_category", True)
        if not matches_category:
            friendly_msg = f"We couldn't find a clear {category_name} in this photo — please upload a photo showing just the item."
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = f"Category mismatch: vision model reported image does not match category '{category_name}'."
            analysis.error_message = friendly_msg
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        analysis.color = str(parsed.get("color", "")).strip()
        analysis.description = str(parsed.get("description", "")).strip()

        # Cloudinary swap
        if analysis.fal_cdn_url:
            try:
                img_resp = await sync_to_async(requests.get)(analysis.fal_cdn_url, timeout=30)
                img_resp.raise_for_status()
                filename = f"processed_item_{analysis.wardrobe_item_id}.png"
                await sync_to_async(analysis.processed_image.save)(filename, ContentFile(img_resp.content), save=False)
            except Exception as exc:
                logger.warning("Cloudinary migration failed for ItemAnalysis %s: %s", analysis.id, exc)

        analysis.status = ItemAnalysis.JobStatus.DONE
        analysis.error_message = ""
        analysis.internal_error_detail = ""
        await analysis.asave(update_fields=["color", "description", "processed_image", "status", "error_message", "internal_error_detail", "updated_at"])

        return Response({"status": "ok"}, status=status.HTTP_200_OK)
