import logging
import fal_client
import requests
from django.core.files.base import ContentFile

from .adapters import get_profile_constraints
from .models import Avatar

logger = logging.getLogger(__name__)


def resolve_user_default_avatar(user, requested_style=None):
    """
    Unified style-strict default avatar resolver:
    1. User's preferred completed avatar matching requested_style.
    2. Latest completed user avatar matching requested_style.
    3. Admin system default matching user gender + requested_style.
    """
    if requested_style not in Avatar.Style.values:
        requested_style = Avatar.Style.REALISTIC

    if user and user.is_authenticated:
        pref_avatar = Avatar.objects.filter(
            user=user,
            is_preferred=True,
            status=Avatar.JobStatus.DONE,
            style=requested_style,
        ).first()
        if pref_avatar:
            return pref_avatar

        latest_avatar = Avatar.objects.filter(
            user=user,
            status=Avatar.JobStatus.DONE,
            style=requested_style,
        ).order_by("-created_at").first()
        if latest_avatar:
            return latest_avatar

    user_gender = "male"
    if user and user.is_authenticated:
        profile = getattr(user, "customer_profile", None)
        if profile and profile.gender in ("male", "female"):
            user_gender = profile.gender

    system_default = Avatar.objects.filter(
        is_default=True,
        gender=user_gender,
        style=requested_style,
    ).first()

    if not system_default:
        system_default = Avatar.objects.filter(is_default=True, style=requested_style).first()

    if not system_default:
        system_default = Avatar.objects.filter(is_default=True).first()

    if not system_default:
        system_default = Avatar.objects.create(
            user=None,
            is_default=True,
            gender=user_gender,
            style=requested_style,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_avatar.png",
        )

    return system_default


AVATAR_STYLE_PROMPTS = {
    Avatar.Style.REALISTIC: (
        "5. VISUAL STYLE & QUALITY STANDARD (Premium Realistic Digital Human Avatar):\n"
        "- Render as a premium realistic digital human avatar: human, believable, and anatomically precise, with realistic skin texture, subtle microdetail, realistic eyes, hair, hands, fabric drape, and natural studio lighting.\n"
        "- The result must retain the polished, cohesive look of an intentionally created digital avatar — NOT an unprocessed raw camera photograph, raw smartphone picture, or documentary photo.\n"
        "- Avoid uncanny plastic/CGI rendering as well as raw photographic camera artifacts. Target: authentic human appearance + polished digital-avatar rendering."
    ),
    Avatar.Style.CARTOON: (
        "5. VISUAL STYLE & QUALITY STANDARD (Polished 3D Cartoon / Stylized Character Avatar):\n"
        "- Render as a polished 3D cartoon / stylized digital character avatar.\n"
        "- Consistent artistic visual language: clean silhouettes, smooth stylized shading, cohesive lighting, charming stylized proportions, and polished facial/hair/clothing rendering.\n"
        "- Coherent stylized artwork: Must look intentionally rendered as ONE complete illustrated piece, with no photorealistic or mismatched textures."
    ),
}

FAL_MODEL_ID = "fal-ai/nano-banana-pro/edit"


def build_avatar_prompt(style: str, profile_constraints: dict = None) -> str:
    """
    Constructs a structured fal.ai prompt that treats Image 1 as a reference to
    extract identity and clothing from, rather than a composition/crop to copy.
    Always generates a complete, standardized full-body avatar (head-to-toe).
    """
    profile_constraints = profile_constraints or {}
    gender_raw = str(profile_constraints.get("gender") or "").strip().lower()

    # 1. Primary Identity & Person Reference Directive (Reference vs Composition)
    identity_lines = [
        "1. PRIMARY IDENTITY & REFERENCE DIRECTIVE (Image 1 is a Reference, NOT a Composition Template):",
        "- Treat Image 1 as a reference to understand the person's physical characteristics, NOT as a photographic composition to copy.",
        "- Do NOT reproduce the original image's camera framing, crop, background, photographic artifacts, or raw photo appearance.",
        "- Extract and faithfully preserve the person's recognizable features: facial structure, face shape, eyes, nose, mouth, jawline, skin tone, hair color, hair texture, hairstyle, approximate age, and likeness.",
    ]

    # Gender handling: explicit user profile constraint if provided, else visual inference from person
    if gender_raw in ("male", "man", "boy", "m"):
        identity_lines.append("- Gender Constraint: The subject is MALE. Maintain consistent male gender presentation.")
    elif gender_raw in ("female", "woman", "girl", "f"):
        identity_lines.append("- Gender Constraint: The subject is FEMALE. Maintain consistent female gender presentation.")
    else:
        identity_lines.append(
            "- Gender Inference: Apparent gender presentation must be directly inferred from the actual person in Image 1. "
            "Do NOT randomly switch or alter male/female presentation. Infer gender from the person's physical features, not solely from clothing."
        )

    identity_prompt = "\n".join(identity_lines)

    # 2. Mandatory Full-Body Avatar Composition & Framing
    body_lines = [
        "2. MANDATORY FULL-BODY AVATAR COMPOSITION (Head-to-Toe Framing):",
        "- MANDATORY FULL-BODY REQUIREMENT: Regardless of whether Image 1 is a face-only close-up, headshot, chest-up portrait, half-body photo, or full-body photo, the output MUST ALWAYS be a complete, head-to-toe full-body avatar.",
        "- The source image crop must NEVER determine the final avatar crop. Do NOT generate portrait-only, chest-up, waist-up, or cropped-leg compositions.",
        "- Construct the complete human body naturally: full head, neck, shoulders, torso, arms, hands, waist, hips, both legs, and feet visible in frame.",
        "- Composition: Clean, centered, full-body character presentation with ample surrounding space against a plain, neutral light gray studio background.",
        "- Pose: Standing naturally in a relaxed, balanced, anatomically natural pose suitable for virtual try-on and wardrobe dressing.",
        "- Accurately preserve visible height, body build, weight distribution, and proportions from Image 1.",
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

    # 3. Authoritative Source Clothing Reconstruction & Partial/Cropped Fallback Hierarchy
    clothing_prompt = (
        "3. AUTHORITATIVE CLOTHING RECONSTRUCTION & PARTIAL-IMAGE FALLBACK HIERARCHY:\n"
        "- PRIORITY 1 (COMPLETE SOURCE CLOTHING): If Image 1 clearly shows complete clothing, faithfully reconstruct that exact outfit (category, silhouette, cut, length, sleeves, collar/neckline, construction, colors, fabric patterns, layering, and headwear such as hijab/abaya/turban if present). Reconstruct the garment naturally rather than pasting pixels.\n"
        "- PRIORITY 2 (PARTIAL SOURCE CLOTHING): If Image 1 shows partial clothing with enough information to identify the garment, preserve what is visible and naturally complete it.\n"
        "- PRIORITY 3 (FALLBACK FOR CROPPED / FACE-ONLY / HALF-BODY SOURCE IMAGES):\n"
        "  * MALE FALLBACK: When the source does not provide enough clothing information to determine the full outfit, it is acceptable to generate a clean, polished coat/jacket with tailored trousers/pants as neutral male avatar attire.\n"
        "  * FEMALE FALLBACK: Do NOT generate a generic coat + pants suit, random Western suit, or abaya (unless the source actually indicates an abaya). When the lower clothing cannot be determined from a face/upper-body/half-body image, generate a modest, elegant, full-length kaftan or loose round long dress that extends naturally to full length (head-to-toe). The dress MUST use the same dominant/visible color or closely matching color family from the source image's clothing/palette (e.g., visible purple/lavender upper garment -> purple/lavender long kaftan/dress; visible blue -> blue long kaftan/dress; visible beige/tan -> beige/tan long kaftan/dress; visible pink -> pink long kaftan/dress). Maintain a simple, polished, loose non-body-hugging silhouette.\n"
        "- Clearly visible source clothing ALWAYS takes strict precedence over fallback templates."
    )

    # 4. Visual Style & Quality Standard
    style_prompt = AVATAR_STYLE_PROMPTS.get(style, AVATAR_STYLE_PROMPTS[Avatar.Style.REALISTIC])

    sections = [
        identity_prompt,
        body_prompt,
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
            if not image_url:
                raise Exception(f"No output image URL found in fal.ai result: {res}")

            if avatar.user and not avatar.is_default:
                Avatar.objects.filter(user=avatar.user, is_preferred=True).exclude(pk=avatar.pk).update(is_preferred=False)
                avatar.is_preferred = True

            avatar.fal_cdn_url = image_url
            avatar.status = Avatar.JobStatus.DONE
            avatar.is_saved = False
            avatar.error_message = ""
            avatar.internal_error_detail = ""
            avatar.save(
                update_fields=[
                    "fal_cdn_url",
                    "status",
                    "is_preferred",
                    "error_message",
                    "internal_error_detail",
                    "updated_at",
                ]
            )
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
