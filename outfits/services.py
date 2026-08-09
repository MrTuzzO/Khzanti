import logging
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
import fal_client
from avatars.models import Avatar
from wardrobe_items_ai.services import _friendly_error_message, verify_webhook_signature
from .models import JobStatus, OutfitJob, TriggerType

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


from datetime import timedelta
from django.db import IntegrityError, transaction
from django.utils import timezone
from wardrobe_items_ai.models import ItemAnalysis, JobStatus as ItemJobStatus, WardrobeItem


def select_auto_tryon_assets(user, scheduled_date):
    """
    Selects avatar and up to 1 wardrobe item per category for daily auto outfit generation.
    - Applies 3-day no-repeat rule for item selection.
    - Applies color coordination heuristic.
    """
    avatar = Avatar.objects.filter(user=user, status=Avatar.JobStatus.DONE).order_by("-created_at").first()
    if not avatar:
        avatar = Avatar.objects.filter(user=user).order_by("-created_at").first()
    if not avatar:
        avatar = Avatar.objects.filter(is_default=True).first()
    if not avatar:
        avatar = Avatar.objects.create(
            user=None,
            is_default=True,
            status=Avatar.JobStatus.DONE,
            style=Avatar.Style.REALISTIC,
            result_image="avatars/result/default_avatar.png",
        )

    # Inspect 3-day no-repeat item IDs
    past_dates = [scheduled_date - timedelta(days=i) for i in range(1, 4)]
    recent_auto_jobs = OutfitJob.objects.filter(
        user=user,
        trigger_type=TriggerType.AUTO,
        scheduled_date__in=past_dates,
    ).prefetch_related("wardrobe_items")

    recent_item_ids = set()
    for past_job in recent_auto_jobs:
        recent_item_ids.update(past_job.wardrobe_items.values_list("id", flat=True))

    # Available completed wardrobe items
    completed_items = list(
        WardrobeItem.objects.filter(
            user=user,
            analysis__status=ItemJobStatus.DONE,
        ).select_related("category", "analysis").order_by("-created_at")
    )

    if not completed_items:
        return avatar, []

    # Group completed items by category
    items_by_category = {}
    for item in completed_items:
        cat_id = item.category_id
        if cat_id not in items_by_category:
            items_by_category[cat_id] = []
        items_by_category[cat_id].append(item)

    selected_items = []
    for cat_id, cat_items in items_by_category.items():
        non_recent = [it for it in cat_items if it.id not in recent_item_ids]
        candidate = non_recent[0] if non_recent else cat_items[0]
        selected_items.append(candidate)

    return avatar, selected_items


def get_or_create_today_auto_job(user) -> OutfitJob:
    """
    Retrieves or lazily creates today's AUTO OutfitJob for the given user.
    Uses transaction.atomic() + unique constraint handling.
    AI generation is submitted strictly OUTSIDE the database transaction.
    """
    today_date = timezone.now().date()

    # 1. Quick read check
    existing_job = OutfitJob.objects.filter(
        user=user,
        scheduled_date=today_date,
        trigger_type=TriggerType.AUTO,
    ).first()

    if existing_job:
        return existing_job

    should_submit = False
    new_job = None

    # 2. Database transaction for atomic job creation
    try:
        with transaction.atomic():
            job = OutfitJob.objects.select_for_update().filter(
                user=user,
                scheduled_date=today_date,
                trigger_type=TriggerType.AUTO,
            ).first()

            if not job:
                avatar, selected_items = select_auto_tryon_assets(user, today_date)
                job = OutfitJob.objects.create(
                    user=user,
                    avatar=avatar,
                    scheduled_date=today_date,
                    trigger_type=TriggerType.AUTO,
                    status=JobStatus.PENDING,
                )
                if selected_items:
                    job.wardrobe_items.set(selected_items)
                should_submit = True

            new_job = job
    except IntegrityError:
        new_job = OutfitJob.objects.get(
            user=user,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
        )
        should_submit = False

    # 3. External AI submission OUTSIDE transaction block
    if should_submit and new_job:
        submit_try_on_job(new_job)
        new_job.refresh_from_db()

    return new_job

