import logging
import fal_client
import requests
from django.core.files.base import ContentFile

from .adapters import get_profile_constraints
from .models import Avatar

logger = logging.getLogger(__name__)


DEFAULT_CLOTHING_PROMPTS = {
    "male": (
        "Default Clothing:\n"
        "Formal professional male attire consisting of a formal shirt, a black formal suit, "
        "professional tailored trousers, and a clean, polished appearance. "
        "Avoid casual T-shirts, shorts, overly fashionable clothing, or distracting patterns."
    ),
    "female": (
        "Default Clothing:\n"
        "Modest, elegant professional female attire suitable for a Saudi/Arabian corporate environment: "
        "a long, loose-fitting formal dress or abaya-style professional outfit with full-length sleeves, "
        "a high covered neckline, full-length coverage, and a loose, non-body-hugging silhouette. "
        "Styling in neutral or dark professional colors such as black, navy, charcoal, beige, or dark brown with minimal accessories. "
        "Do NOT generate tight-fitting trousersuits, body-hugging dresses, short skirts, low necklines, exposed arms/shoulders, "
        "sheer fabrics, or revealing silhouettes. Do NOT automatically add a headscarf or hijab unless present in Image 1 or profile."
    ),
    "default": (
        "Default Clothing:\n"
        "Modest, elegant formal professional attire with full coverage, loose non-body-hugging fit, and clean, polished styling."
    ),
}

AVATAR_STYLE_PROMPTS = {
    Avatar.Style.REALISTIC: (
        "Identity & Facial Characteristics:\n"
        "Keep the exact same facial features as Image 1 — same eyes, nose shape, "
        "jawline, skin tone, and hairstyle.\n\n"
        "Visual Style:\n"
        "Semi-realistic 3D rendered character style, full-body, standing, front-facing, "
        "arms relaxed, with soft studio lighting against a plain light gray background."
    ),
    Avatar.Style.CARTOON: (
        "Identity & Facial Characteristics:\n"
        "Preserve the person's identity and recognizable facial characteristics from Image 1 — "
        "same hairstyle, skin tone, eyes, nose, and distinct facial features.\n\n"
        "Visual Style:\n"
        "Polished 3D cartoon character style, full-body, standing, front-facing, "
        "arms relaxed, with soft studio lighting against a plain light gray background."
    ),
}

FAL_MODEL_ID = "fal-ai/nano-banana-pro/edit"


def build_avatar_prompt(style: str, profile_constraints: dict = None) -> str:
    """
    Constructs a structured fal.ai prompt that clearly separates:
    1. Identity / Physical Characteristics (Image 1 reference)
    2. Body / Proportions (visible height, build, weight distribution + profile attributes)
    3. Appearance (skin tone, hair characteristics)
    4. Default Clothing (gender-specific formal/modest attire)
    5. Visual Style (semi-realistic or 3D cartoon)
    """
    profile_constraints = profile_constraints or {}
    gender_raw = str(profile_constraints.get("gender") or "").strip().lower()

    if gender_raw in ("male", "man", "boy", "m"):
        clothing_prompt = DEFAULT_CLOTHING_PROMPTS["male"]
    elif gender_raw in ("female", "woman", "girl", "f"):
        clothing_prompt = DEFAULT_CLOTHING_PROMPTS["female"]
    else:
        clothing_prompt = DEFAULT_CLOTHING_PROMPTS["default"]

    if style == Avatar.Style.CARTOON:
        identity_prompt = (
            "Identity & Physical Characteristics:\n"
            "Preserve the person's identity and recognizable facial characteristics from Image 1 — "
            "same eyes, nose, jawline, skin tone, hairstyle, and distinct facial features."
        )
        style_prompt = (
            "Visual Style:\n"
            "Polished 3D cartoon character style, full-body, standing, front-facing, "
            "arms relaxed, with soft studio lighting against a plain light gray background."
        )
    else:
        identity_prompt = (
            "Identity & Physical Characteristics:\n"
            "Keep the exact same facial features as Image 1 — same eyes, nose shape, "
            "jawline, skin tone, hairstyle, and facial proportions."
        )
        style_prompt = (
            "Visual Style:\n"
            "Semi-realistic 3D rendered character style, full-body, standing, front-facing, "
            "arms relaxed, with soft studio lighting against a plain light gray background."
        )

    body_lines = [
        "Body & Proportions:\n"
        "Treat Image 1 as the primary visual source for body characteristics, accurately "
        "preserving visible height, body build, weight distribution, overall body shape, and proportions."
    ]

    profile_lines = []
    if profile_constraints.get("gender"):
        profile_lines.append(f"Gender: {profile_constraints['gender']}")
    if profile_constraints.get("height"):
        profile_lines.append(f"Height: {profile_constraints['height']}")
    if profile_constraints.get("age"):
        profile_lines.append(f"Age: {profile_constraints['age']}")
    if profile_constraints.get("body_type"):
        profile_lines.append(f"Body build/type: {profile_constraints['body_type']}")
    elif profile_constraints.get("build"):
        profile_lines.append(f"Body build/type: {profile_constraints['build']}")
    if profile_constraints.get("weight"):
        profile_lines.append(f"Weight: {profile_constraints['weight']}")

    if profile_lines:
        body_lines.append("User Profile Guidance (reinforcing reference image):\n- " + "\n- ".join(profile_lines))

    body_prompt = "\n".join(body_lines)

    appearance_prompt = (
        "Appearance:\n"
        "Retain exact skin tone, hair characteristics, hair color, texture, and all clearly visible physical characteristics from Image 1."
    )

    sections = [
        identity_prompt,
        body_prompt,
        appearance_prompt,
        clothing_prompt,
        style_prompt,
    ]

    return "\n\n".join(sections)


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
        try:
            selfie_url = avatar.source_photo.url if avatar.source_photo else ""
        except Exception:
            selfie_url = f"http://testserver/{avatar.source_photo.name}" if avatar.source_photo else ""
        profile_constraints = get_profile_constraints(avatar.user)
        prompt = build_avatar_prompt(avatar.style, profile_constraints)

        handle = fal_client.submit(
            FAL_MODEL_ID,
            arguments={
                "prompt": prompt,
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

            avatar.fal_cdn_url = image_url
            avatar.status = Avatar.JobStatus.DONE
            avatar.is_saved = False
            avatar.error_message = ""
            avatar.internal_error_detail = ""
            avatar.save(update_fields=["fal_cdn_url", "status", "is_saved", "error_message", "internal_error_detail", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Avatar job %s status sync failed: %s", avatar.id, raw_error, exc_info=True)
        avatar.status = Avatar.JobStatus.FAILED
        avatar.internal_error_detail = raw_error
        avatar.error_message = _friendly_error_message(raw_error)
        avatar.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return avatar


def save_avatar_to_cloudinary(avatar: Avatar) -> Avatar:
    """
    Explicitly downloads the generated avatar from fal.ai CDN URL and uploads it to Cloudinary storage.
    Idempotent: if already saved, returns immediately without re-uploading.
    """
    if avatar.is_saved:
        return avatar

    url_to_download = avatar.fal_cdn_url or (avatar.result_image.url if avatar.result_image else "")
    if not url_to_download:
        from core.exceptions import ServiceError
        raise ServiceError(
            detail="Avatar does not have a valid generated image to save.",
            status_code=400,
        )

    try:
        img_resp = requests.get(url_to_download, timeout=30)
        img_resp.raise_for_status()

        filename = f"avatar_{avatar.id}.png"
        avatar.result_image.save(filename, ContentFile(img_resp.content), save=False)
        avatar.is_saved = True
        avatar.save(update_fields=["result_image", "is_saved", "updated_at"])
    except Exception as exc:
        from core.exceptions import ServiceError
        if isinstance(exc, ServiceError):
            raise exc
        raw_error = str(exc)
        logger.error("Avatar %s save to Cloudinary failed: %s", avatar.id, raw_error, exc_info=True)
        raise ServiceError(
            detail="Failed to save avatar image. Please try again later.",
            debug_detail=raw_error,
            status_code=502,
        )

    return avatar
