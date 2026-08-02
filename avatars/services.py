import logging
import fal_client
import requests
from django.core.files.base import ContentFile
from .models import Avatar

logger = logging.getLogger(__name__)

AVATAR_STYLE_PROMPT = (
    "Keep the exact same facial features as image 1 — same eyes, nose shape, "
    "jawline, skin tone, hairstyle. Change everything else: render the person "
    "full-body, standing, front-facing, arms relaxed, in a semi-realistic 3D "
    "rendered character style with soft studio lighting, against a plain light "
    "gray background. Casual modern outfit."
)

FAL_MODEL_ID = "fal-ai/nano-banana-pro/edit"


def _friendly_error_message(raw_error: str) -> str:
    err_lower = (raw_error or "").lower()
    if any(kw in err_lower for kw in ("balance", "locked", "billing", "credit")):
        return "The avatar generation service is currently unavailable. Please try again later."
    if any(kw in err_lower for kw in ("safety", "policy", "content", "nsfw")):
        return "The source photo could not be processed. Please try uploading a different photo."
    if "timeout" in err_lower:
        return "The request timed out while generating your avatar. Please try again."
    return "Failed to generate avatar. Please try again later."


def submit_avatar_job(avatar: Avatar) -> None:
    """
    Submits an avatar generation job to fal.ai.
    Saves the request ID and updates status to 'processing'.
    On submission failure, updates status to 'failed' with friendly error details.
    """
    try:
        selfie_url = avatar.source_photo.url
        handle = fal_client.submit(
            FAL_MODEL_ID,
            arguments={
                "prompt": AVATAR_STYLE_PROMPT,
                "image_urls": [selfie_url],
                "resolution": "1K",
                "output_format": "png",
            },
        )
        avatar.fal_request_id = handle.request_id
        avatar.status = Avatar.JobStatus.PROCESSING
        avatar.save(update_fields=["fal_request_id", "status", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Avatar job %s submission failed: %s", avatar.id, raw_error, exc_info=True)
        avatar.status = Avatar.JobStatus.FAILED
        avatar.internal_error_detail = raw_error
        avatar.error_message = _friendly_error_message(raw_error)
        avatar.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])


def sync_avatar_status(avatar: Avatar) -> Avatar:
    """
    Checks job status with fal.ai and updates DB accordingly.
    Idempotent: returns immediately if avatar is already 'done', 'failed',
    or lacks a fal_request_id.
    """
    if (
        avatar.status in (Avatar.JobStatus.DONE, Avatar.JobStatus.FAILED)
        or not avatar.fal_request_id
    ):
        return avatar

    try:
        status_info = fal_client.status(FAL_MODEL_ID, avatar.fal_request_id)
        if isinstance(status_info, fal_client.Completed):
            if getattr(status_info, "error", None):
                raise Exception(f"fal.ai error: {status_info.error}")

            res = fal_client.result(FAL_MODEL_ID, avatar.fal_request_id)
            image_url = None
            if isinstance(res, dict):
                if res.get("images") and isinstance(res["images"], list) and len(res["images"]) > 0:
                    first_img = res["images"][0]
                    if isinstance(first_img, dict):
                        image_url = first_img.get("url")
                    elif isinstance(first_img, str):
                        image_url = first_img
                elif res.get("image"):
                    img_val = res.get("image")
                    if isinstance(img_val, dict):
                        image_url = img_val.get("url")
                    elif isinstance(img_val, str):
                        image_url = img_val

            if not image_url:
                raise Exception(f"No output image URL found in fal.ai result: {res}")

            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()

            filename = f"avatar_{avatar.id}.png"
            avatar.result_image.save(filename, ContentFile(img_resp.content), save=False)
            avatar.status = Avatar.JobStatus.DONE
            avatar.error_message = ""
            avatar.internal_error_detail = ""
            avatar.save()
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Avatar job %s status sync failed: %s", avatar.id, raw_error, exc_info=True)
        avatar.status = Avatar.JobStatus.FAILED
        avatar.internal_error_detail = raw_error
        avatar.error_message = _friendly_error_message(raw_error)
        avatar.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return avatar
