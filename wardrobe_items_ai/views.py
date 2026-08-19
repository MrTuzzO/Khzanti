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
        if hasattr(fresh_item, "analysis"):
            _raise_if_analysis_failed(fresh_item.analysis)

        headers = self.get_success_headers(serializer.data)
        read_serializer = WardrobeItemSerializer(fresh_item, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class WardrobeItemDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WardrobeItemSerializer

    def get_queryset(self):
        return WardrobeItem.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {"detail": "Wardrobe item deleted successfully."},
            status=status.HTTP_200_OK,
        )



class ItemAnalysisStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ItemAnalysisSerializer

    def get_queryset(self):
        return ItemAnalysis.objects.filter(wardrobe_item__user=self.request.user).select_related("wardrobe_item", "wardrobe_item__category")

    def get_object(self):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        pk_val = self.kwargs[lookup_url_kwarg]
        queryset = self.filter_queryset(self.get_queryset())
        analysis = (
            queryset.filter(wardrobe_item_id=pk_val).first()
            or queryset.filter(pk=pk_val).first()
        )
        if not analysis:
            raise Http404("Item analysis not found.")
        _raise_if_analysis_failed(analysis)
        return analysis






@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class BgRemovalWebhookView(AsyncAPIView):
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
            "[WARDROBE AI WEBHOOK BG_REMOVAL] Received request: payload_req_id=%s req_id=%s sig_present=%s status=%s",
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
                logger.warning("[WARDROBE AI WEBHOOK BG_REMOVAL] Signature verification failed for request_id=%s", payload_req_id)
                return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_403_FORBIDDEN)

        if not payload_req_id:
            logger.warning("[WARDROBE AI WEBHOOK BG_REMOVAL] Missing request_id in payload and headers")
            return Response({"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            analysis = await ItemAnalysis.objects.select_related("wardrobe_item", "wardrobe_item__category").aget(
                fal_request_id_bg_removal=payload_req_id,
            )
        except ItemAnalysis.DoesNotExist:
            logger.warning("[WARDROBE AI WEBHOOK BG_REMOVAL] ItemAnalysis not found for fal_request_id_bg_removal=%s", payload_req_id)
            return Response({"detail": "Matching processing job not found."}, status=status.HTTP_400_BAD_REQUEST)

        status_str = data.get("status")
        payload_body = data.get("payload") if "payload" in data else data

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "Background removal failed on fal.ai"
            logger.error("[WARDROBE AI WEBHOOK BG_REMOVAL] fal.ai returned error for ItemAnalysis %s: %s", analysis.id, err_msg)
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
            logger.error("[WARDROBE AI WEBHOOK BG_REMOVAL] No image URL extracted from payload for ItemAnalysis %s: %s", analysis.id, payload_body)
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = f"No image URL returned in payload: {payload_body}"
            analysis.error_message = "Failed to remove image background."
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        analysis.fal_cdn_url = image_url
        await analysis.asave(update_fields=["fal_cdn_url", "updated_at"])
        logger.info("[WARDROBE AI WEBHOOK BG_REMOVAL] Saved fal_cdn_url=%s for ItemAnalysis %s. Submitting Vision job...", image_url, analysis.id)

        await submit_vision_job_async(analysis)
        return Response({"status": "ok"}, status=status.HTTP_200_OK)



@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
class VisionWebhookView(AsyncAPIView):
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
            "[WARDROBE AI WEBHOOK VISION] Received request: payload_req_id=%s req_id=%s sig_present=%s status=%s",
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
                logger.warning("[WARDROBE AI WEBHOOK VISION] Signature verification failed for request_id=%s", payload_req_id)
                return Response({"detail": "Invalid webhook signature."}, status=status.HTTP_403_FORBIDDEN)

        if not payload_req_id:
            logger.warning("[WARDROBE AI WEBHOOK VISION] Missing request_id in payload and headers")
            return Response({"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            analysis = await ItemAnalysis.objects.select_related("wardrobe_item", "wardrobe_item__category").aget(
                fal_request_id_vision=payload_req_id,
            )
        except ItemAnalysis.DoesNotExist:
            logger.warning("[WARDROBE AI WEBHOOK VISION] ItemAnalysis not found for fal_request_id_vision=%s", payload_req_id)
            return Response({"detail": "Matching processing job not found."}, status=status.HTTP_400_BAD_REQUEST)

        status_str = data.get("status")
        payload_body = data.get("payload") if "payload" in data else data

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "Vision analysis failed on fal.ai"
            logger.error("[WARDROBE AI WEBHOOK VISION] fal.ai returned error for ItemAnalysis %s: %s", analysis.id, err_msg)
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
        detected_item_type = str(parsed.get("detected_item_type", "")).strip()

        matches_category = parsed.get("matches_category", True)
        if not matches_category:
            friendly_msg = f"We couldn't find a clear {category_name} in this photo — please upload a photo showing just the item."
            logger.warning(
                "[WARDROBE AI WEBHOOK VISION] Category mismatch for ItemAnalysis %s (detected_item_type='%s'): %s",
                analysis.id,
                detected_item_type,
                friendly_msg,
            )
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = (
                f"Category mismatch: vision model reported image (detected item: '{detected_item_type or 'unknown'}') "
                f"does not match category '{category_name}'."
            )
            analysis.error_message = friendly_msg
            await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        analysis.color = str(parsed.get("color", "")).strip()
        analysis.description = str(parsed.get("description", "")).strip()
        analysis.status = ItemAnalysis.JobStatus.DONE
        analysis.is_saved = False
        analysis.error_message = ""
        analysis.internal_error_detail = ""
        await analysis.asave(
            update_fields=["color", "description", "status", "is_saved", "error_message", "internal_error_detail", "updated_at"]
        )
        logger.info(
            "[WARDROBE AI WEBHOOK VISION] Successfully completed analysis for ItemAnalysis %s (WardrobeItem %s, detected_item_type='%s')",
            analysis.id,
            analysis.wardrobe_item_id,
            detected_item_type,
        )

        return Response({"status": "ok"}, status=status.HTTP_200_OK)


class WardrobeItemSaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={200: WardrobeItemSerializer},
        description="Save a completed wardrobe item analysis result permanently to Cloudinary storage.",
    )
    def post(self, request, pk, *args, **kwargs):
        try:
            item = WardrobeItem.objects.select_related("category", "analysis").get(
                pk=pk, user=request.user
            )
        except WardrobeItem.DoesNotExist:
            raise Http404("Wardrobe item not found.")

        analysis = getattr(item, "analysis", None)
        if not analysis or analysis.status != ItemAnalysis.JobStatus.DONE:
            return Response(
                {"detail": "Wardrobe item analysis is not completed yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not analysis.fal_cdn_url:
            return Response(
                {"detail": "No processed image URL available to save."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotent check: return existing saved object without re-downloading/re-uploading
        if analysis.is_saved and analysis.processed_image:
            serializer = WardrobeItemSerializer(item, context={"request": request})
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Download from fal_cdn_url and save to Cloudinary
        try:
            resp = requests.get(analysis.fal_cdn_url, timeout=30)
            resp.raise_for_status()
            filename = f"processed_item_{item.id}.png"
            analysis.processed_image.save(filename, ContentFile(resp.content), save=False)
            analysis.is_saved = True
            analysis.save(update_fields=["processed_image", "is_saved", "updated_at"])
        except Exception as exc:
            logger.error("Failed to save wardrobe item %s processed image to Cloudinary: %s", item.id, exc, exc_info=True)
            return Response(
                {"detail": "Failed to store processed image to Cloudinary storage. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        item.refresh_from_db()
        serializer = WardrobeItemSerializer(item, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
