import json
import logging
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from wardrobe_items_ai.services import verify_webhook_signature
from .models import ConversionStatus, ThreeDConversion
from .serializers import ConvertRequestSerializer, ThreeDConversionSerializer
from .services import _friendly_3d_error_message, submit_3d_conversion

logger = logging.getLogger(__name__)


def extract_mesh_url(payload: dict | None) -> str | None:
    """
    Extracts the .glb mesh URL from the fal.ai Hunyuan 3D v3.1 Pro webhook payload.
    Supports official Hunyuan 3D v3.1 structures:
    - model_glb.url
    - model_urls.glb.url
    Ensures non-GLB files (e.g. JPG visualizations, PNG textures, PLY/OBJ files) are ignored.
    """
    if not isinstance(payload, dict):
        return None

    # Primary Hunyuan 3D v3.1 output structure
    model_glb = payload.get("model_glb")
    if isinstance(model_glb, dict) and model_glb.get("url"):
        return model_glb["url"]

    model_urls = payload.get("model_urls")
    if isinstance(model_urls, dict):
        glb_info = model_urls.get("glb")
        if isinstance(glb_info, dict) and glb_info.get("url"):
            return glb_info["url"]

    # Explicit GLB or mesh keys
    for key in ("glb", "model_mesh", "mesh", "file", "model", "result"):
        val = payload.get(key)
        if isinstance(val, dict) and val.get("url"):
            url = val["url"]
            if ".glb" in url.lower() or "glb" in key:
                return url
        if isinstance(val, str) and val.startswith("http") and ".glb" in val.lower():
            return val

    # Direct top-level URL if it is a GLB
    url_val = payload.get("url")
    if isinstance(url_val, str) and url_val.startswith("http") and ".glb" in url_val.lower():
        return url_val

    # Scan for dictionary values containing a .glb URL
    for key, val in payload.items():
        if key in ("visualization", "thumbnail", "preview", "texture_urls", "textures", "meshes"):
            continue
        if isinstance(val, dict) and val.get("url"):
            url = val["url"]
            if ".glb" in url.lower():
                return url

    return None


class ConvertCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ConvertRequestSerializer,
        responses={201: ThreeDConversionSerializer, 200: ThreeDConversionSerializer},
        description="Converts a completed OutfitJob image into a downloadable 3D model (.glb) using fal.ai Hunyuan 3D v3.1 Pro.",
    )
    def post(self, request, *args, **kwargs):
        serializer = ConvertRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        outfit_job_id = serializer.validated_data["outfit_job_id"]
        conversion, created = submit_3d_conversion(request.user, outfit_job_id)

        response_serializer = ThreeDConversionSerializer(
            conversion, context={"request": request}
        )

        http_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        message = "3D conversion started." if created else "Existing 3D conversion retrieved."

        return Response(
            {
                "status": "success",
                "code": http_code,
                "message": message,
                "data": response_serializer.data,
            },
            status=http_code,
        )


class ConversionDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ThreeDConversionSerializer

    def get_queryset(self):
        return ThreeDConversion.objects.filter(outfit_job__user=self.request.user)

    @extend_schema(
        responses={200: ThreeDConversionSerializer},
        description="Retrieves status and result mesh URL of a 3D conversion owned by the user.",
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {
                "status": "success",
                "code": status.HTTP_200_OK,
                "message": "3D conversion status.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class ConversionWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, request, *args, **kwargs):
        raw_body = request.body
        req_id = (
            request.headers.get("X-Fal-Webhook-Request-Id")
            or request.headers.get("X-Fal-Request-Id")
            or ""
        )
        user_id = request.headers.get("X-Fal-Webhook-User-Id") or ""
        timestamp = request.headers.get("X-Fal-Webhook-Timestamp") or ""
        sig_hex = request.headers.get("X-Fal-Webhook-Signature") or ""

        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            data = {}

        payload_req_id = data.get("request_id") or req_id

        logger.info(
            "[3D CONVERSION WEBHOOK] Received webhook: payload_req_id=%s req_id=%s sig_present=%s status=%s",
            payload_req_id,
            req_id,
            bool(sig_hex),
            data.get("status"),
        )
        logger.info("[3D CONVERSION] Webhook received for request %s", payload_req_id)

        if sig_hex:
            is_valid = verify_webhook_signature(
                req_id, user_id, timestamp, sig_hex, raw_body
            )
            if not is_valid:
                logger.warning(
                    "[3D CONVERSION WEBHOOK] Signature verification failed for request_id=%s",
                    payload_req_id,
                )
                return Response(
                    {"detail": "Invalid webhook signature."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if not payload_req_id:
            logger.warning("[3D CONVERSION WEBHOOK] Missing request_id in payload and headers")
            return Response(
                {"detail": "Missing request_id."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            conversion = ThreeDConversion.objects.get(fal_request_id=payload_req_id)
        except ThreeDConversion.DoesNotExist:
            logger.warning(
                "[3D CONVERSION WEBHOOK] ThreeDConversion not found for fal_request_id=%s",
                payload_req_id,
            )
            return Response(
                {"detail": "Matching 3D conversion job not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotency check
        if conversion.status in (ConversionStatus.DONE, ConversionStatus.FAILED):
            logger.info(
                "[3D CONVERSION WEBHOOK] Conversion %s already in terminal state (%s). Skipping duplicate callback.",
                conversion.id,
                conversion.status,
            )
            return Response(
                {"status": "already_processed", "job_status": conversion.status},
                status=status.HTTP_200_OK,
            )

        status_str = data.get("status")
        payload_body = data.get("payload") if "payload" in data else data

        if status_str != "OK" or data.get("error"):
            err_msg = data.get("error") or str(payload_body) or "3D model conversion failed on fal.ai"
            logger.error(
                "[3D CONVERSION WEBHOOK] fal.ai returned error for ThreeDConversion %s: %s",
                conversion.id,
                err_msg,
            )
            logger.error("[3D CONVERSION] Hunyuan generation failed for conversion %s: %s", conversion.id, err_msg)
            conversion.status = ConversionStatus.FAILED
            conversion.error_message = _friendly_3d_error_message(str(err_msg))
            conversion.save(update_fields=["status", "error_message", "updated_at"])
            return Response({"status": "error_handled"}, status=status.HTTP_200_OK)

        mesh_url = extract_mesh_url(payload_body)
        if not mesh_url:
            logger.error(
                "[3D CONVERSION WEBHOOK] No mesh URL found in payload for ThreeDConversion %s",
                conversion.id,
            )
            conversion.status = ConversionStatus.FAILED
            conversion.error_message = "3D model generation completed but no GLB model URL was returned."
            conversion.save(update_fields=["status", "error_message", "updated_at"])
            return Response({"status": "missing_mesh_url"}, status=status.HTTP_200_OK)

        conversion.result_mesh_url = mesh_url
        conversion.status = ConversionStatus.DONE
        conversion.error_message = ""
        conversion.save(update_fields=["result_mesh_url", "status", "error_message", "updated_at"])

        logger.info("[3D CONVERSION] Hunyuan generation completed")
        logger.info("[3D CONVERSION] GLB URL extracted successfully")
        logger.info("[3D CONVERSION] Conversion %s marked DONE", conversion.id)
        return Response({"status": "success"}, status=status.HTTP_200_OK)
