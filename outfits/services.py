import logging
import requests
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
import fal_client

from avatars.models import Avatar
from core.exceptions import ServiceError
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
    if avatar_obj.is_saved and avatar_obj.result_image:
        try:
            avatar_image_url = avatar_obj.result_image.url
        except Exception:
            pass
    if not avatar_image_url and avatar_obj.fal_cdn_url:
        avatar_image_url = avatar_obj.fal_cdn_url
    if not avatar_image_url and avatar_obj.source_photo:
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


def save_try_on_to_cloudinary(job: OutfitJob) -> OutfitJob:
    """
    Explicitly downloads the generated try-on result image from fal.ai CDN URL and uploads it to Cloudinary storage.
    Idempotent: if already saved, returns immediately without re-uploading.
    Works for both MANUAL and AUTO try-ons.
    """
    if job.is_saved:
        return job

    url_to_download = job.fal_cdn_url or (job.result_image.url if job.result_image else "")
    if not url_to_download:
        raise ServiceError(
            detail="Try-on job does not have a valid generated image to save.",
            status_code=400,
        )

    try:
        img_resp = requests.get(url_to_download, timeout=30)
        img_resp.raise_for_status()

        filename = f"outfit_tryon_{job.id}.png"
        job.result_image.save(filename, ContentFile(img_resp.content), save=False)
        job.is_saved = True
        job.save(update_fields=["result_image", "is_saved", "updated_at"])
    except ServiceError:
        raise
    except Exception as exc:
        raw_error = str(exc)
        logger.error("OutfitJob %s save to Cloudinary failed: %s", job.id, raw_error, exc_info=True)
        raise ServiceError(
            detail="Failed to save try-on image. Please try again later.",
            debug_detail=raw_error,
            status_code=502,
        )

    return job


from datetime import timedelta
from django.db import IntegrityError, transaction
from django.utils import timezone
from wardrobe_items_ai.models import ItemAnalysis, JobStatus as ItemJobStatus, WardrobeItem


from datetime import timedelta
import json
import os
from django.db import IntegrityError, transaction
from django.utils import timezone
from openai import OpenAI
from wardrobe_items_ai.models import ItemAnalysis, JobStatus as ItemJobStatus, WardrobeItem


from avatars.services import resolve_user_default_avatar


def resolve_tryon_avatar(user, requested_style=None):
    """
    Delegates to unified resolve_user_default_avatar service.
    """
    return resolve_user_default_avatar(user, requested_style=requested_style)


def select_auto_tryon_assets(user, scheduled_date):
    """
    Fallback asset selector when OpenAI selection is bypassed or in fallback mode.
    - Selects completed avatar following priority chain.
    - Selects up to 1 item per category applying 3-day no-repeat rule.
    """
    avatar = resolve_tryon_avatar(user)

    past_dates = [scheduled_date - timedelta(days=i) for i in range(1, 4)]
    recent_auto_jobs = OutfitJob.objects.filter(
        user=user,
        trigger_type=TriggerType.AUTO,
        scheduled_date__in=past_dates,
    ).prefetch_related("wardrobe_items")

    recent_item_ids = set()
    for past_job in recent_auto_jobs:
        recent_item_ids.update(past_job.wardrobe_items.values_list("id", flat=True))

    completed_items = list(
        WardrobeItem.objects.filter(
            user=user,
            analysis__status=ItemJobStatus.DONE,
        ).select_related("category", "analysis").order_by("-created_at")
    )

    if not completed_items:
        return avatar, []

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


def select_auto_outfit_combination(user, scheduled_date):
    """
    OpenAI-powered wardrobe combination selector for daily AUTO outfits.
    Gathers candidate completed wardrobe items and past 3-day combinations,
    invokes OpenAI to select the best styling combination with structured reasoning,
    and performs 8 strict backend validation checks on the AI response.
    """
    avatar, _ = select_auto_tryon_assets(user, scheduled_date)

    completed_items = list(
        WardrobeItem.objects.filter(
            user=user,
            analysis__status=ItemJobStatus.DONE,
        ).select_related("category", "analysis").order_by("-created_at")
    )

    if not completed_items:
        logger.warning("[AUTO OUTFIT] User %s has 0 completed wardrobe items.", user.id)
        return avatar, [], {
            "title": "No Wardrobe Items Available",
            "subtitle": "Upload items to generate daily outfits",
            "reasons": [],
            "style_note": "Please upload and process wardrobe items first."
        }

    candidate_map = {item.id: item for item in completed_items}
    candidate_data = [
        {
            "id": item.id,
            "category": getattr(item.category, "name", "Clothing"),
            "season": getattr(item, "season", "") or "any",
            "occasion": getattr(item, "occasion", "") or "any",
            "color": getattr(item.analysis, "color", "") or "unknown",
            "description": getattr(item.analysis, "description", "") or "",
        }
        for item in completed_items
    ]

    past_dates = [scheduled_date - timedelta(days=i) for i in range(1, 4)]
    past_jobs = OutfitJob.objects.filter(
        user=user,
        trigger_type=TriggerType.AUTO,
        scheduled_date__in=past_dates,
    ).prefetch_related("wardrobe_items")

    past_combinations = [
        list(j.wardrobe_items.values_list("id", flat=True))
        for j in past_jobs
    ]

    weekday_name = scheduled_date.strftime("%A")
    date_str = scheduled_date.isoformat()

    logger.info(
        "[AUTO OUTFIT] Preparing selection for user %s on %s (%s): %d candidates, %d past 3-day combinations",
        user.id,
        date_str,
        weekday_name,
        len(candidate_data),
        len(past_combinations),
    )

    api_key = getattr(settings, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    model_name = getattr(settings, "OPENAI_MODEL", "gpt-5.1") or os.getenv("OPENAI_MODEL", "gpt-5.1")

    if not api_key:
        logger.warning("[AUTO OUTFIT] OPENAI_API_KEY is not set. Falling back to default selection.")
        _, fallback_items = select_auto_tryon_assets(user, scheduled_date)
        return avatar, fallback_items, {
            "title": "Daily Outfit Recommendation",
            "subtitle": "Smart automated outfit combination",
            "reasons": [{"title": "Default Selection", "description": "Automated style pairing"}],
            "style_note": f"A balanced look curated for {weekday_name}."
        }

    try:
        logger.info("[AUTO OUTFIT] OpenAI selection started using model '%s'...", model_name)
        client = OpenAI(api_key=api_key)
        system_prompt = (
            "You are an expert AI fashion stylist for Fashion Hub AI (Hdoomi).\n"
            "Your goal is to select the optimal wardrobe combination for today's daily AUTO outfit for the user.\n\n"
            "STRICT SELECTION RULES:\n"
            "1. Select item IDs ONLY from the provided candidate list. Never invent or hallucinate item IDs.\n"
            "2. Select MAXIMUM 1 item per category.\n"
            "3. Apply visual color harmony, season appropriateness, occasion fit, and aesthetic coordination.\n"
            "4. Avoid repeating the exact combination of item IDs used in any of the previous 3 days if alternative candidate combinations exist.\n"
            "5. Return strictly a JSON object adhering to the JSON schema."
        )

        user_prompt = f"""
Today's Date: {date_str} ({weekday_name})

Candidate Wardrobe Items (Choose ONLY from these IDs):
{json.dumps(candidate_data, indent=2)}

Past 3 Days' AUTO Combinations (Avoid exact repeats if alternatives exist):
{json.dumps(past_combinations)}

Return JSON adhering to:
{{
  "selected_item_ids": [integer_id_1, integer_id_2, ...],
  "reasoning": {{
    "title": "Title for today's look",
    "subtitle": "Short subtitle summarizing the aesthetic",
    "reasons": [
      {{"title": "Style Matched", "description": "..."}},
      {{"title": "Color Harmony", "description": "..."}}
    ],
    "style_note": "Final styling tip"
  }}
}}
"""

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "content", "content": user_prompt} if hasattr(client, "_dummy") else {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)
    except Exception as exc:
        logger.error("[AUTO OUTFIT] OpenAI API call failed: %s", exc, exc_info=True)
        raise ValueError(f"OpenAI API call failed: {exc}")

    # --- 8-POINT BACKEND VALIDATION ---
    selected_ids = parsed.get("selected_item_ids")
    if not isinstance(selected_ids, list):
        raise ValueError("OpenAI response invalid: 'selected_item_ids' must be a list.")

    if not selected_ids:
        raise ValueError("OpenAI response invalid: 'selected_item_ids' list is empty.")

    selected_items = []
    for item_id in selected_ids:
        if not isinstance(item_id, int):
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid non-integer item ID returned: {item_id}")

        if item_id not in candidate_map:
            raise ValueError(f"OpenAI returned invalid or unowned item ID #{item_id}.")
        selected_items.append(candidate_map[item_id])

    if len(selected_items) != len(set(it.id for it in selected_items)):
        raise ValueError("OpenAI response invalid: duplicate item IDs selected.")

    seen_categories = {}
    for item in selected_items:
        cat_id = item.category_id
        if cat_id in seen_categories:
            raise ValueError(
                f"OpenAI response invalid: multiple items selected for category ID {cat_id} (items #{seen_categories[cat_id].id} and #{item.id})."
            )
        seen_categories[cat_id] = item

    reasoning = parsed.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}

    reasoning_dict = {
        "title": str(reasoning.get("title", f"Daily Look for {weekday_name}")),
        "subtitle": str(reasoning.get("subtitle", "Curated AI Outfit")),
        "reasons": reasoning.get("reasons", []),
        "style_note": str(reasoning.get("style_note", "")),
    }

    logger.info(
        "[AUTO OUTFIT] OpenAI selection completed & validated. Selected item IDs: %s. Title: '%s'",
        [it.id for it in selected_items],
        reasoning_dict["title"],
    )

    return avatar, selected_items, reasoning_dict


def get_or_create_today_auto_job(user) -> OutfitJob:
    """
    Retrieves or lazily creates today's AUTO OutfitJob for the given user.
    Uses transaction.atomic() + unique constraint handling.
    OpenAI selection and fal.ai generation are performed strictly OUTSIDE database transactions.
    """
    today_date = timezone.now().date()

    logger.info("[AUTO OUTFIT] GET /today/ request for user %s on date %s", user.id, today_date)

    # 1. Read check: If today's AUTO job already exists, return it (NO OpenAI call, NO fal.ai call, NO cost!)
    existing_job = OutfitJob.objects.filter(
        user=user,
        scheduled_date=today_date,
        trigger_type=TriggerType.AUTO,
    ).first()

    if existing_job:
        logger.info("[AUTO OUTFIT] Existing AUTO job found (ID %s, status=%s). Returning cached job.", existing_job.id, existing_job.status)
        return existing_job

    logger.info("[AUTO OUTFIT] No existing AUTO job found for user %s on date %s. Initiating generation.", user.id, today_date)

    # 2. Perform AI Wardrobe Combination Selection OUTSIDE transaction block
    avatar = None
    selected_items = []
    reasoning_dict = {}
    ai_failed = False
    ai_error_msg = ""

    try:
        avatar, selected_items, reasoning_dict = select_auto_outfit_combination(user, today_date)
    except Exception as exc:
        logger.error("[AUTO OUTFIT] AI wardrobe combination selection failed for user %s: %s", user.id, exc)
        ai_failed = True
        ai_error_msg = str(exc)

    if ai_failed:
        avatar = Avatar.objects.filter(user=user).order_by("-created_at").first() or Avatar.objects.filter(is_default=True).first()
        if not avatar:
            avatar = Avatar.objects.create(
                user=None, is_default=True, status=Avatar.JobStatus.DONE, style=Avatar.Style.REALISTIC
            )
        job = OutfitJob.objects.create(
            user=user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            error_message="Daily outfit selection failed.",
            internal_error_detail=ai_error_msg,
        )
        logger.info("[AUTO OUTFIT] Created FAILED OutfitJob %s due to AI selection error.", job.id)
        return job

    if not selected_items:
        avatar = avatar or Avatar.objects.filter(is_default=True).first()
        job = OutfitJob.objects.create(
            user=user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            error_message="No completed wardrobe items available for daily outfit generation.",
            internal_error_detail="No items with completed ItemAnalysis status found.",
        )
        logger.info("[AUTO OUTFIT] Created FAILED OutfitJob %s due to missing completed wardrobe items.", job.id)
        return job

    should_submit = False
    new_job = None

    # 3. Database transaction for atomic job creation
    try:
        with transaction.atomic():
            job = OutfitJob.objects.select_for_update().filter(
                user=user,
                scheduled_date=today_date,
                trigger_type=TriggerType.AUTO,
            ).first()

            if not job:
                job = OutfitJob.objects.create(
                    user=user,
                    avatar=avatar,
                    scheduled_date=today_date,
                    trigger_type=TriggerType.AUTO,
                    status=JobStatus.PENDING,
                    reasoning_title=reasoning_dict.get("title", ""),
                    reasoning_subtitle=reasoning_dict.get("subtitle", ""),
                    reasoning_items=reasoning_dict.get("reasons", []),
                    reasoning_note=reasoning_dict.get("style_note", ""),
                )
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

    # 4. External fal.ai submission OUTSIDE transaction block
    if should_submit and new_job and new_job.status == JobStatus.PENDING:
        logger.info("[AUTO OUTFIT] Submitting newly created OutfitJob %s to fal.ai Nano Banana Pro...", new_job.id)
        submit_try_on_job(new_job)
        new_job.refresh_from_db()
        logger.info("[AUTO OUTFIT] fal.ai submission finished for OutfitJob %s (status=%s, fal_request_id=%s)", new_job.id, new_job.status, new_job.fal_request_id)

    return new_job


