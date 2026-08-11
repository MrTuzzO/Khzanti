import logging
from django.conf import settings
from django.db import transaction
import fal_client
from rest_framework.exceptions import NotFound, ValidationError

from outfits.models import JobStatus as OutfitJobStatus, OutfitJob
from .models import ConversionStatus, ThreeDConversion

logger = logging.getLogger(__name__)

FAL_HUNYUAN_3D_MODEL_ID = "fal-ai/hunyuan-3d/v3.1/pro/image-to-3d"


def _friendly_3d_error_message(raw_error: str) -> str:
    """
    Returns a user-friendly, non-sensitive error message for 3D model generation failures.
    Does NOT leak internal API keys, tokens, stack traces, or wardrobe-specific messages.
    """
    err_lower = (raw_error or "").lower()
    if any(kw in err_lower for kw in ("balance", "locked", "billing", "credit")):
        return "The 3D model generation service is currently unavailable. Please try again later."
    if any(kw in err_lower for kw in ("safety", "policy", "content", "nsfw")):
        return "The outfit image could not be converted due to safety policies. Please try another outfit."
    if "timeout" in err_lower:
        return "The request timed out while generating the 3D model. Please try again."
    return "3D model generation failed. Please try again later."


def get_outfit_job_for_user(user, outfit_job_id: int) -> OutfitJob:
    """
    Validates and fetches the OutfitJob for the given user.
    Raises NotFound (404) if the job doesn't exist or doesn't belong to user.
    Raises ValidationError (400) if the outfit is not ready for 3D conversion.
    """
    try:
        outfit_job = OutfitJob.objects.get(pk=outfit_job_id, user=user)
    except OutfitJob.DoesNotExist:
        logger.warning("[3D CONVERSION] OutfitJob %s not found for user %s", outfit_job_id, user.id)
        raise NotFound("Outfit job not found.")

    if outfit_job.status in (OutfitJobStatus.PENDING, OutfitJobStatus.PROCESSING):
        logger.warning(
            "[3D CONVERSION] OutfitJob %s status is %s; not ready for conversion.",
            outfit_job.id,
            outfit_job.status,
        )
        raise ValidationError("This outfit is not ready for 3D conversion yet.")

    if outfit_job.status == OutfitJobStatus.FAILED:
        logger.warning("[3D CONVERSION] OutfitJob %s status is failed.", outfit_job.id)
        raise ValidationError("Cannot convert a failed outfit job.")

    if outfit_job.status != OutfitJobStatus.DONE or not outfit_job.result_image:
        logger.warning(
            "[3D CONVERSION] OutfitJob %s lacks completed image (status=%s, result_image=%s)",
            outfit_job.id,
            outfit_job.status,
            bool(outfit_job.result_image),
        )
        raise ValidationError("Outfit job does not have a completed image for 3D conversion.")

    return outfit_job


def submit_3d_conversion(user, outfit_job_id: int) -> tuple[ThreeDConversion, bool]:
    """
    Submits a Hunyuan 3D v3.1 Pro conversion request for the user's completed OutfitJob.
    Returns tuple (ThreeDConversion, created_boolean).
    Idempotent: Returns existing conversion if an active (pending/processing) or done conversion exists.
    """
    logger.info("[3D CONVERSION] Starting Hunyuan 3D conversion for outfit %s", outfit_job_id)

    outfit_job = get_outfit_job_for_user(user, outfit_job_id)
    logger.info("[3D CONVERSION] OutfitJob validated")

    # Deduplication check
    existing = (
        ThreeDConversion.objects.filter(
            outfit_job=outfit_job,
            status__in=[
                ConversionStatus.PENDING,
                ConversionStatus.PROCESSING,
                ConversionStatus.DONE,
            ],
        )
        .order_by("-created_at")
        .first()
    )

    if existing:
        logger.info(
            "[3D CONVERSION] Active or completed conversion %s found for OutfitJob %s (status=%s). Returning existing.",
            existing.id,
            outfit_job.id,
            existing.status,
        )
        return existing, False

    # Create DB record outside external API lock
    with transaction.atomic():
        conversion = ThreeDConversion.objects.create(
            outfit_job=outfit_job,
            status=ConversionStatus.PENDING,
        )
        logger.info("[3D CONVERSION] Creating ThreeDConversion %s", conversion.id)

    base_url = (getattr(settings, "WEBHOOK_BASE_URL", "") or "").rstrip("/")
    webhook_url = f"{base_url}/api/v1/model3d/webhook/"

    image_url = outfit_job.result_image.url

    logger.info("[3D CONVERSION] Submitting image to fal.ai Hunyuan 3D v3.1 Pro")

    try:
        handle = fal_client.submit(
            FAL_HUNYUAN_3D_MODEL_ID,
            arguments={
                "input_image_url": image_url,
            },
            webhook_url=webhook_url,
        )
        conversion.fal_request_id = handle.request_id
        conversion.status = ConversionStatus.PROCESSING
        conversion.save(update_fields=["fal_request_id", "status", "updated_at"])
        logger.info("[3D CONVERSION] Hunyuan request submitted: %s", handle.request_id)
        logger.info("[3D CONVERSION] Conversion marked processing")
    except Exception as exc:
        raw_error = str(exc)
        logger.error(
            "[3D CONVERSION] Hunyuan generation failed for conversion %s: %s",
            conversion.id,
            raw_error,
            exc_info=True,
        )
        conversion.status = ConversionStatus.FAILED
        conversion.error_message = _friendly_3d_error_message(raw_error)
        conversion.save(update_fields=["status", "error_message", "updated_at"])

    return conversion, True
