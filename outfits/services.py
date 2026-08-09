import logging
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
import fal_client
from avatars.models import Avatar
from wardrobe_items_ai.services import _friendly_error_message, verify_webhook_signature
from .models import JobStatus, OutfitJob

logger = logging.getLogger(__name__)

FAL_TRY_ON_MODEL_ID = "fal-ai/nano-banana-pro/edit"


def build_try_on_prompt(avatar: Avatar, items: list) -> str:
    """
    Constructs a highly detailed, instruction-rich prompt for fal-ai/nano-banana-pro/edit.
    Establishes Image 1 as the Avatar base subject, followed by reference wardrobe items (Image 2, 3, etc.).
    """
    avatar_style_desc = (
        "photorealistic human avatar" if avatar.style == Avatar.Style.REALISTIC else "cartoon/stylized avatar"
    )
    style_preservation_instruction = (
        "Preserve the exact photorealistic human appearance, natural skin texture, face structure, hairstyle, and body proportions of the base avatar."
        if avatar.style == Avatar.Style.REALISTIC
        else "Preserve the exact cartoon/stylized art style, character design, facial features, and body proportions of the base avatar. Do NOT convert into a realistic human."
    )

    prompt_parts = [
        f"HIGH PRIORITY VIRTUAL TRY-ON VISUALIZATION:",
        f"1. BASE SUBJECT (Image 1): The person in Image 1 is the primary base subject ({avatar_style_desc}). {style_preservation_instruction} Keep the avatar's face, identity, pose, and overall visual composition intact.",
        f"2. WARDROBE REFERENCE ITEMS:",
    ]

    for idx, item in enumerate(items, start=2):
        category_name = getattr(item.category, "name", "clothing item")
        analysis = getattr(item, "analysis", None)
        color = getattr(analysis, "color", "") if analysis else ""
        color_str = f" in color {color}" if color else ""
        prompt_parts.append(
            f"   - Image {idx} represents the reference {category_name}{color_str}. "
            f"You MUST retain this garment's exact color, fabric appearance, material, texture, pattern, print, embroidery, seams, silhouette, and distinctive design elements."
        )

    prompt_parts.extend([
        "3. NATURAL GARMENT FITTING & PLACEMENT:",
        "- Reconstruct and fit each reference item naturally onto the avatar's body.",
        "- Borkha / dresses / tops / pants must be worn on the torso and body.",
        "- Hijab / headwear must be naturally worn on the head and neck.",
        "- Shoes / footwear must be naturally worn on the feet.",
        "- Bags / accessories must be held or worn in appropriate contact points.",
        "4. LAYERING, OCCLUSION & CLOTHING INTERACTION:",
        "- Ensure physically natural clothing overlap (e.g., Hijab drapes over the head and shoulders, layering neatly around the neckline over the Borkha).",
        "- Account for natural garment draping, body contouring, folds, depth, contact points, lighting, and realistic shadows.",
        "- Do NOT paste items as flat stickers, do NOT leave clothing floating or beside the subject.",
        "5. STRICT NO INVENTED ITEMS:",
        "- Include ONLY the selected reference wardrobe items worn by the avatar.",
        "- Do NOT invent or add any extra unselected clothing layers, random jewelry, or unrequested accessories.",
        f"The final output must be a single, cohesive, high-quality visual try-on image maintaining the avatar's exact visual style ({avatar.style})."
    ])

    return "\n".join(prompt_parts)


async def submit_try_on_job_async(job: OutfitJob) -> OutfitJob:
    """
    Asynchronously submits a single virtual try-on request to fal-ai/nano-banana-pro/edit.
    """
    avatar_obj = await sync_to_async(lambda: job.avatar)()
    items = await sync_to_async(lambda: list(job.wardrobe_items.select_related("category", "analysis").all()))()

    # Build image URLs starting with Avatar
    avatar_image_url = ""
    if avatar_obj.result_image:
        try:
            avatar_image_url = avatar_obj.result_image.url
        except Exception:
            pass
    elif avatar_obj.source_photo:
        try:
            avatar_image_url = avatar_obj.source_photo.url
        except Exception:
            pass

    image_urls = [avatar_image_url]

    for item in items:
        analysis = getattr(item, "analysis", None)
        item_url = analysis.display_url if analysis else ""
        if item_url:
            image_urls.append(item_url)

    prompt = build_try_on_prompt(avatar_obj, items)

    base_url = (getattr(settings, "WEBHOOK_BASE_URL", "") or "").rstrip("/")
    webhook_url = f"{base_url}/api/v1/outfits/try-on/webhook/"

    logger.info(
        "[OUTFITS TRY-ON DEBUG] Submitting OutfitJob %s with %d images to fal.ai (avatar style=%s)",
        job.id,
        len(image_urls),
        avatar_obj.style,
    )

    try:
        handle = await sync_to_async(fal_client.submit)(
            FAL_TRY_ON_MODEL_ID,
            arguments={
                "prompt": prompt,
                "image_urls": image_urls,
            },
            webhook_url=webhook_url,
        )
        job.fal_request_id = handle.request_id
        job.status = JobStatus.PROCESSING
        job.error_message = ""
        job.internal_error_detail = ""
        await job.asave(update_fields=["fal_request_id", "status", "error_message", "internal_error_detail", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("[OUTFITS TRY-ON] Submission failed for OutfitJob %s: %s", job.id, raw_error, exc_info=True)
        job.status = JobStatus.FAILED
        job.internal_error_detail = raw_error
        job.error_message = _friendly_error_message(raw_error)
        await job.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return job


def submit_try_on_job(job: OutfitJob) -> OutfitJob:
    """
    Synchronous wrapper around submit_try_on_job_async.
    """
    return async_to_sync(submit_try_on_job_async)(job)
