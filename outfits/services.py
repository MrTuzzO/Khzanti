import logging
import re
import requests
from asgiref.sync import async_to_sync, sync_to_async

from django.conf import settings
from django.core.files.base import ContentFile
import fal_client

from avatars.models import Avatar
from core.exceptions import ServiceError
from wardrobe_items_ai.services import _friendly_error_message, verify_webhook_signature
from .models import JobStatus, OutfitJob, SavedOutfit, TriggerType

logger = logging.getLogger(__name__)

FAL_TRY_ON_MODEL_ID = "fal-ai/nano-banana-pro/edit"


def build_try_on_prompt(avatar: Avatar, items: list) -> str:
    """
    Constructs an instruction-rich prompt for fal-ai/nano-banana-pro/edit.
    Enforces a strict division of responsibilities:
      - Image 1 (Avatar) = Authoritative Final-Image World (controls person, pose, lighting, camera, environment, style).
      - Wardrobe Images = Authoritative Garment References (determines WHAT garment is worn: category, cut, color, pattern, construction).
    Directs the model to reconstruct the garments naturally on the avatar rather than compositing or pasting pixels.
    """
    is_realistic = (avatar.style == Avatar.Style.REALISTIC)
    avatar_style_desc = "photorealistic human photograph" if is_realistic else "cartoon / stylized / illustrated artwork"

    if is_realistic:
        style_specific_instructions = (
            "4. STYLE EXECUTION — PHOTOREALISTIC RENDERING (ONE-CAMERA / ONE-PHOTOGRAPH RULE):\n"
            "- Render all clothing with the EXACT SAME photographic realism and optical characteristics as the avatar in Image 1.\n"
            "- The garments must display authentic fabric microtexture, realistic material response to light, subtle tonal variations, physically plausible folds, and natural edge softness against the skin.\n"
            "- The entire image must share the avatar's exact single light source, lighting direction, shadow softness, exposure, white balance, color temperature, contrast, tonal range, depth of field, sharpness, image grain, and camera lens characteristics.\n"
            "- There must be ZERO difference in rendering fidelity between the person's skin and the clothing.\n"
            "- STRICTLY PROHIBITED: Do NOT make the clothing look cleaner, flatter, sharper, smoother, or more digitally rendered than the person. The clothing must NEVER look like a 3D asset, sticker, cutout, pasted layer, illustration, or digitally painted overlay."
        )
    else:
        style_specific_instructions = (
            "4. STYLE EXECUTION — CARTOON / ILLUSTRATED / STYLIZED RENDERING:\n"
            "- Reconstruct all garments in the EXACT SAME visual language and artistic medium as the avatar in Image 1.\n"
            "- Match the avatar's rendering technique, line quality, outline thickness, flat/cel/gradient shading, highlights, color palette treatment, texture abstraction, and level of artistic detail.\n"
            "- Any photorealistic wardrobe reference photo must be completely stylized and redrawn to match the avatar's illustrated world.\n"
            "- NEVER place a photorealistic garment or real photographic texture onto a cartoon avatar."
        )

    prompt_parts = [
        "MANDATORY EXECUTION DIRECTIVE — ONE-SHOT COHESIVE VIRTUAL TRY-ON & GARMENT RECONSTRUCTION:",
        "",
        f"1. IMAGE 1 — AVATAR CONTROLS THE AUTHORITATIVE VISUAL WORLD ({avatar_style_desc}):",
        "- The avatar in Image 1 is the AUTHORITATIVE BASE SCENE that dictates the entire visual world of the final output.",
        "- Avatar controls: person identity, face, facial structure, expression, apparent gender, skin appearance, hair, body proportions, anatomy, pose, camera position, camera angle, framing, perspective, environment, background, lighting, exposure, image quality, and overall rendering style.",
        "- STRICT PRESERVATION: Do NOT repaint, restyle, regenerate, beautify, or alter the avatar. Adapt the clothing to the person — NEVER alter the person to fit the clothing.",
        "",
        "2. WARDROBE IMAGES ARE AUTHORITATIVE GARMENT REFERENCES (WHAT TO WEAR, NOT HOW TO RENDER):",
        "- The wardrobe images (Image 2, 3, etc.) control ONLY the garment's identity and design specifications:",
        "  * Garment category, garment identity, silhouette, construction, cut, length, proportions, color, fabric pattern, neckline/collar, sleeves, cuffs, buttons, pockets, seams, and distinctive details.",
        "- RECONSTRUCT, DO NOT COPY DEFECTS: A blurry, poorly lit, wrinkled, or flat-lay photographed wardrobe reference must become a clean, realistic, physically fitted version of THAT EXACT SAME GARMENT on the avatar.",
        "- STRICTLY DISCARD all wardrobe image source artifacts: DO NOT copy wardrobe background, lighting, shadows, flat-lay folds/wrinkles, camera perspective, noise, distortion, or cutout edges.",
        "- STRICT GARMENT FIDELITY (NO SUBSTITUTION): Do NOT redesign or substitute garments (dress ≠ shirt + pants; dress ≠ coat + pants; skirt ≠ trousers; saree ≠ generic dress; hijab ≠ loose hair; long garment ≠ short garment). Preserve the essential identity and construction of the supplied garment.",
    ]

    for idx, item in enumerate(items, start=2):
        category_name = getattr(item.category, "name", "clothing item")
        analysis = getattr(item, "analysis", None)
        color = getattr(analysis, "color", "") if analysis else ""
        desc = getattr(analysis, "description", "") if analysis else ""
        details = []
        if color:
            details.append(f"color: {color}")
        if desc:
            details.append(f"description: {desc}")
        details_str = f" ({', '.join(details)})" if details else ""
        prompt_parts.append(
            f"   - Image {idx}: Reference {category_name}{details_str}. "
            f"Extract and faithfully reconstruct this garment's design identity (type, cut, silhouette, collar, sleeves, seams, color, pattern, and details)."
        )

    prompt_parts.extend([
        "",
        "3. NATURAL 3D GARMENT RECONSTRUCTION (NO COMPOSITING / NO PASTING):",
        "- The model must reconstruct each garment as if it were physically present on the avatar when the avatar image was originally created.",
        "- The clothing must naturally conform to the avatar's 3D anatomy: shoulders, chest, torso, waist, hips, arms, legs, body curvature, and pose.",
        "- Obey realistic physical fabric behavior: natural draping, fabric tension, compression folds, gravity, seams, contact shadows, ambient occlusion, and soft edge blending.",
        "- MULTI-GARMENT PHYSICAL LAYERING: When multiple items are worn, maintain proper physical layering (e.g., shirts tucked into/under pants or jackets, dresses under coats, hijabs draped around head/neck contouring over shoulders and collar, shoes attached naturally to feet).",
        "- NEVER treat the wardrobe image as a texture, cutout, sticker, pasted layer, or pixel source.",
        "",
        style_specific_instructions,
        "",
        "5. VISUAL COHERENCE & ULTIMATE QUALITY TEST:",
        "- CRITICAL TEST: A human observer looking at the final image must perceive that the person was naturally photographed or created wearing the supplied outfit.",
        "- It must NEVER look like someone took a wardrobe product image and photoshopped it onto the person.",
        "- Include ONLY the specified wardrobe items. Do NOT invent unrequested accessories, extra layers, or unselected garments.",
        "",
        "6. PRIORITY HIERARCHY WHEN REFERENCES CONFLICT:",
        "   1. Avatar's visual world: identity, anatomy, pose, lighting, camera, perspective, and background environment (Authoritative)",
        "   2. Natural physical integration and fabric draping on the avatar's body",
        "   3. Garment design identity from the wardrobe reference (category, silhouette, color, pattern, cut)",
        "   4. Minor decorative details from the wardrobe reference",
        "",
        f"FINAL OUTPUT: A single, seamlessly unified masterwork where the {avatar_style_desc} appears naturally and authentically dressed in the requested wardrobe outfit."
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


def sync_outfit_job_status(job: OutfitJob) -> OutfitJob:
    """
    Checks try-on job status with fal.ai and updates DB accordingly.
    Fallback when webhooks are missed/delayed (e.g. local ngrok offline).
    Idempotent: returns immediately if job is already DONE, FAILED, or has no fal_request_id.
    """
    if job.status in (JobStatus.DONE, JobStatus.FAILED) or not job.fal_request_id:
        return job

    try:
        status_info = fal_client.status(FAL_TRY_ON_MODEL_ID, job.fal_request_id)
        if isinstance(status_info, fal_client.Completed):
            if getattr(status_info, "error", None):
                raise Exception(f"fal.ai error: {status_info.error}")

            res = fal_client.result(FAL_TRY_ON_MODEL_ID, job.fal_request_id)
            image_url = None
            if isinstance(res, dict):
                if res.get("image") and isinstance(res["image"], dict):
                    image_url = res["image"].get("url")
                elif res.get("image") and isinstance(res["image"], str):
                    image_url = res["image"]
                elif res.get("images") and isinstance(res["images"], list) and len(res["images"]) > 0:
                    first_img = res["images"][0]
                    image_url = first_img.get("url") if isinstance(first_img, dict) else first_img

            if not image_url:
                raise Exception(f"No image URL returned in fal.ai result: {res}")

            job.fal_cdn_url = image_url
            job.status = JobStatus.DONE
            job.is_saved = False
            job.error_message = ""
            job.internal_error_detail = ""
            job.save(update_fields=["fal_cdn_url", "status", "is_saved", "error_message", "internal_error_detail", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("OutfitJob %s status sync failed: %s", job.id, raw_error, exc_info=True)
        job.status = JobStatus.FAILED
        job.internal_error_detail = raw_error
        job.error_message = _friendly_error_message(raw_error)
        job.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return job


def save_try_on_to_cloudinary(job: OutfitJob, saved_date=None) -> OutfitJob:
    """
    Explicitly downloads the generated try-on result image from fal.ai CDN URL and uploads it to Cloudinary storage,
    sets is_saved=True, and creates the corresponding SavedOutfit entry for the user.
    Works for both MANUAL and AUTO try-ons.
    """
    url_to_download = job.fal_cdn_url or (job.result_image.url if job.result_image else "")
    if not url_to_download and not job.is_saved:
        raise ServiceError(
            detail="Try-on job does not have a valid generated image to save.",
            status_code=400,
        )

    with transaction.atomic():
        if not job.is_saved:
            try:
                if not job.result_image and url_to_download:
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

        from django.utils import timezone
        target_date = saved_date or job.scheduled_date or timezone.now().date()
        SavedOutfit.objects.get_or_create(
            user=job.user,
            outfit_job=job,
            defaults={"date": target_date},
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


from avatars.adapters import get_profile_constraints
from avatars.services import resolve_user_default_avatar

def categorize_item_semantics(category_name: str) -> dict:
    """
    Semantically parses a category name string and classifies it into structural and gender groups.
    Handles compound names (e.g. 'Tops & T-Shirts'), plurals, case variations, and aliases.
    """
    raw = (category_name or "").strip().lower()

    full_body_kw = [
        "dress", "dresses", "jumpsuit", "jumpsuits", "romper", "rompers",
        "borkha", "burqa", "abaya", "abayas", "gown", "gowns", "frock", "frocks",
        "saree", "sari", "sarees", "suit", "suits", "thobe", "thobes", "kandura",
        "one-piece", "salwar kameez", "lehenga", "kaftan", "overalls", "dungaree"
    ]
    top_kw = [
        "top", "tops", "shirt", "shirts", "t-shirt", "t-shirts", "tee", "tees",
        "blouse", "blouses", "sweater", "sweaters", "hoodie", "hoodies",
        "pullover", "pullovers", "tank", "polo", "turtleneck", "camisole", "tunic", "kurta"
    ]
    bottom_kw = [
        "bottom", "bottoms", "pant", "pants", "trouser", "trousers", "jean", "jeans",
        "short", "shorts", "skirt", "skirts", "legging", "leggings", "sweatpant",
        "sweatpants", "slacks", "chinos"
    ]
    outerwear_kw = [
        "outerwear", "jacket", "jackets", "coat", "coats", "blazer", "blazers",
        "cardigan", "cardigans", "vest", "vests", "parka", "windbreaker", "overcoat"
    ]
    shoes_kw = [
        "shoe", "shoes", "footwear", "sneaker", "sneakers", "boot", "boots",
        "heel", "heels", "sandal", "sandals", "flat", "flats", "loafer", "loafers",
        "slipper", "slippers"
    ]
    accessory_kw = [
        "accessory", "accessories", "bag", "bags", "handbag", "purse", "backpack",
        "hijab", "hijabs", "headwear", "hat", "hats", "cap", "caps", "belt", "belts",
        "scarf", "scarves", "tie", "ties", "watch", "jewelry", "sunglasses"
    ]
    feminine_kw = [
        "dress", "dresses", "borkha", "burqa", "abaya", "abayas", "skirt", "skirts",
        "gown", "gowns", "frock", "frocks", "saree", "sari", "sarees", "lehenga",
        "blouse", "blouses", "camisole", "hijab", "hijabs"
    ]

    words = re.findall(r"\w+", raw)

    def matches(keywords):
        return any(w in keywords for w in words) or any(kw in raw for kw in keywords)

    is_full_body = matches(full_body_kw)
    is_outerwear = matches(outerwear_kw)
    is_shoes = matches(shoes_kw)
    is_accessory = matches(accessory_kw)
    is_top = matches(top_kw) and not is_full_body and not is_outerwear
    is_bottom = matches(bottom_kw) and not is_full_body and not is_outerwear

    is_feminine_only = matches(feminine_kw)

    if is_full_body:
        group = "full_body"
    elif is_outerwear:
        group = "outerwear"
    elif is_shoes:
        group = "shoes"
    elif is_accessory:
        group = "accessory"
    elif is_top:
        group = "top"
    elif is_bottom:
        group = "bottom"
    else:
        group = "other"

    return {
        "is_full_body": is_full_body,
        "is_top": is_top,
        "is_bottom": is_bottom,
        "is_outerwear": is_outerwear,
        "is_shoes": is_shoes,
        "is_accessory": is_accessory,
        "is_feminine_only": is_feminine_only,
        "group": group,
    }


def get_estimated_season(scheduled_date, country: str = "") -> str:
    month = scheduled_date.month
    c_lower = (country or "").lower()
    southern_countries = {"australia", "new zealand", "south africa", "argentina", "chile", "brazil", "uruguay"}
    is_southern = any(sc in c_lower for sc in southern_countries)

    if month in (12, 1, 2):
        return "Winter" if not is_southern else "Summer"
    elif month in (3, 4, 5):
        return "Spring" if not is_southern else "Autumn"
    elif month in (6, 7, 8):
        return "Summer" if not is_southern else "Winter"
    else:
        return "Autumn" if not is_southern else "Spring"


def get_user_usable_wardrobe_items(user) -> list:
    """
    Returns all wardrobe items belonging to the user that meet the exact
    usability criteria used by manual try-on:
    - item.user == user
    - analysis exists with status == ItemJobStatus.DONE ('done')
    - analysis has a usable image (display_url or processed_image)
    """
    if not user or not getattr(user, "is_authenticated", True):
        return []

    items = list(
        WardrobeItem.objects.filter(
            user=user,
            analysis__status=ItemJobStatus.DONE,
        ).select_related("category", "analysis").order_by("-created_at")
    )

    usable = []
    for item in items:
        analysis = getattr(item, "analysis", None)
        if analysis and (analysis.display_url or analysis.processed_image):
            usable.append(item)

    return usable


def analyze_garment_semantics(item) -> dict:
    """
    Extracts deep semantic attributes from a WardrobeItem and its ItemAnalysis:
    Combines Category name and AI ItemAnalysis description/color.
    Returns:
    - category_name: str
    - group: str ('full_body', 'top', 'bottom', 'outerwear', 'shoes', 'accessory', 'other')
    - is_full_body, is_top, is_bottom, is_outerwear, is_shoes, is_accessory: bool
    - gender_presentation: str ('female', 'male', 'unisex')
    - is_heavy_outerwear: bool
    - description: str
    - color: str
    """
    cat_name = getattr(item.category, "name", "") or ""
    cat_semantics = categorize_item_semantics(cat_name)

    analysis = getattr(item, "analysis", None)
    description = (getattr(analysis, "description", "") or "").strip()
    color = (getattr(analysis, "color", "") or "").strip()
    desc_lower = description.lower()
    cat_lower = cat_name.lower()
    combined_text = f"{cat_lower} {desc_lower}"

    # 1. Gender Presentation Analysis
    feminine_desc_kw = [
        "dress", "dresses", "bodice", "brooch at the waist", "bustier", "cleavage",
        "feminine", "women's", "woman's", "lady's", "ladies", "gown", "gowns",
        "skirt", "skirts", "saree", "sari", "sarees", "blouse", "blouses",
        "frock", "frocks", "lehenga", "crop top", "heels", "stilettos", "corset",
        "borkha", "burqa", "abaya", "abayas", "draped bodice"
    ]
    masculine_desc_kw = [
        "thobe", "kandura", "jubba", "men's", "man's", "gentleman", "tuxedo",
        "sherwani", "kurta for men", "boxers"
    ]

    import re
    # Remove formal phrases like 'dress shirt', 'dress pants', 'dress shoes', 'dress suit'
    # so that formal male garments containing 'dress' are not misclassified as feminine dresses.
    sanitized_text = re.sub(r"\bdress\s+(shirt|pants|trousers|shoes|suit|socks|belt|coat|vest)s?\b", "", combined_text)

    has_fem_keyword = any(re.search(r"\b" + re.escape(kw) + r"\b", sanitized_text) for kw in feminine_desc_kw)
    has_masc_keyword = any(re.search(r"\b" + re.escape(kw) + r"\b", sanitized_text) for kw in masculine_desc_kw)

    if cat_semantics["is_feminine_only"]:
        gender_presentation = "female"
    elif has_fem_keyword and not has_masc_keyword:
        gender_presentation = "female"
    elif has_masc_keyword and not has_fem_keyword:
        gender_presentation = "male"
    else:
        gender_presentation = "unisex"

    # 2. Warmth / Heavy Layering Analysis
    heavy_outerwear_kw = [
        "trench coat", "overcoat", "parka", "heavy coat", "wool coat", "down jacket",
        "puffer jacket", "winter coat", "duffle coat", "shearling"
    ]
    is_heavy_outerwear = any(kw in combined_text for kw in heavy_outerwear_kw)

    return {
        "item_id": item.id,
        "category_name": cat_name,
        "group": cat_semantics["group"],
        "is_full_body": cat_semantics["is_full_body"],
        "is_top": cat_semantics["is_top"],
        "is_bottom": cat_semantics["is_bottom"],
        "is_outerwear": cat_semantics["is_outerwear"],
        "is_shoes": cat_semantics["is_shoes"],
        "is_accessory": cat_semantics["is_accessory"],
        "gender_presentation": gender_presentation,
        "is_heavy_outerwear": is_heavy_outerwear,
        "description": description,
        "color": color,
    }


def is_garment_gender_compatible(semantics: dict, user_gender: str) -> bool:
    gender = (user_gender or "").strip().lower()
    if gender in ("male", "man", "boy", "m"):
        if semantics["gender_presentation"] == "female":
            return False
    return True


def is_garment_weather_compatible(semantics: dict, estimated_season: str, country: str = "") -> bool:
    c_lower = (country or "").lower()
    hot_countries = {"bangladesh", "united arab emirates", "uae", "saudi arabia", "qatar", "india", "singapore", "thailand", "egypt"}
    is_hot_region = any(hc in c_lower for hc in hot_countries)

    if estimated_season == "Summer" and is_hot_region:
        if semantics["is_heavy_outerwear"]:
            return False
    return True


def filter_eligible_wardrobe_items(items, profile_constraints: dict, scheduled_date=None) -> list:
    """
    Filters wardrobe items based on user profile constraints (gender, weather/season).
    Excludes feminine-only clothing for male profiles.
    Falls back to gender-compatible items if strict weather/season filtering excludes all options.
    """
    gender = str(profile_constraints.get("gender") or "").strip().lower()
    country = str(profile_constraints.get("country") or "").strip()
    season = get_estimated_season(scheduled_date, country) if scheduled_date else "Summer"

    gender_compatible = []
    for item in items:
        semantics = analyze_garment_semantics(item)
        if is_garment_gender_compatible(semantics, gender):
            gender_compatible.append(item)

    if not gender_compatible:
        return []

    weather_compatible = []
    for item in gender_compatible:
        semantics = analyze_garment_semantics(item)
        if is_garment_weather_compatible(semantics, season, country):
            weather_compatible.append(item)

    return weather_compatible or gender_compatible


def validate_outfit_semantic_suitability(selected_items: list, profile_constraints: dict, scheduled_date=None) -> tuple[bool, str]:
    """
    Evaluates whether a list of WardrobeItem objects is semantically suitable for the user profile & date context.
    Returns (is_valid: bool, reason: str).
    """
    if not selected_items:
        return False, "No items selected."

    gender = str(profile_constraints.get("gender") or "").strip().lower()
    country = str(profile_constraints.get("country") or "").strip()
    season = get_estimated_season(scheduled_date, country) if scheduled_date else "Summer"

    semantics_list = [analyze_garment_semantics(item) for item in selected_items]

    # 1. Gender compatibility check
    for s in semantics_list:
        if not is_garment_gender_compatible(s, gender):
            return False, f"Garment '{s['category_name']}' ({s['description']}) has female gender presentation, which is incompatible with male user profile."

    # 2. Weather/Season compatibility check
    for s in semantics_list:
        if not is_garment_weather_compatible(s, season, country):
            return False, f"Garment '{s['category_name']}' is too heavy for {season} season in {country}."

    # 3. Layering & Style Harmonization
    has_light_fullbody = any(s["is_full_body"] and not s["is_heavy_outerwear"] for s in semantics_list)
    has_heavy_coat = any(s["is_heavy_outerwear"] for s in semantics_list)
    if has_light_fullbody and has_heavy_coat and season == "Summer":
        return False, "Incompatible style layering: Heavy coat paired with light summer full-body garment."

    return True, "Valid outfit semantic suitability."


def validate_outfit_composition(selected_items: list) -> tuple[bool, str]:
    """
    Evaluates whether a list of WardrobeItem objects forms a valid, structurally coherent outfit.
    Prevents contradictory combinations (e.g. Full-body + Pants, Full-body + Top, duplicate structural groups).
    Does NOT require every category or specific combination to be present for incomplete wardrobes.
    Allows generation even with 1 item, shirt only, shoes only, or incomplete category combinations.
    Returns (is_valid: bool, reason: str).
    """
    if not selected_items:
        return False, "No wardrobe items selected."

    semantics_list = [categorize_item_semantics(getattr(item.category, "name", "")) for item in selected_items]
    groups = [s["group"] for s in semantics_list]

    # Check 1: Duplicate structural groups (e.g. 2 tops, 2 bottoms, 2 full-body garments)
    if len(groups) != len(set(groups)):
        return False, "Multiple items selected from the same structural clothing group."

    has_full_body = any(s["is_full_body"] for s in semantics_list)
    has_top = any(s["is_top"] for s in semantics_list)
    has_bottom = any(s["is_bottom"] for s in semantics_list)

    # Check 2: Full-body garment compatibility (Cannot combine Full-body with Top or Bottom)
    if has_full_body:
        if has_top or has_bottom:
            return False, "Invalid outfit composition: Full-body garment cannot be combined with separate top or bottom."
        return True, "Valid full-body outfit composition."

    # Check 3: Any available item or combination (1 item, shirt only, shoes only, top+bottom, etc.) is valid!
    return True, "Valid outfit composition."


def resolve_tryon_avatar(user, requested_style=None):
    """
    Delegates to unified resolve_user_default_avatar service.
    """
    return resolve_user_default_avatar(user, requested_style=requested_style)


def resolve_auto_daily_avatar(user):
    """
    Avatar selection specifically for daily AUTOMATIC outfit generation:
    PRIORITY 1:
    Use the user's MOST RECENTLY GENERATED AVATAR if that avatar has actually
    been successfully saved/available on Cloudinary (user=user, is_default=False,
    status=DONE, is_saved=True, non-empty result_image).

    PRIORITY 2:
    If there is no generated avatar with a valid Cloudinary image, use the user's
    DEFAULT AVATAR (is_default=True).
    """
    if user and user.is_authenticated:
        generated_avatar = (
            Avatar.objects.filter(
                user=user,
                is_default=False,
                status=Avatar.JobStatus.DONE,
                is_saved=True,
            )
            .exclude(result_image="")
            .exclude(result_image__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if generated_avatar:
            return generated_avatar

    user_gender = "male"
    if user and user.is_authenticated:
        profile = getattr(user, "customer_profile", None)
        if profile and profile.gender in ("male", "female"):
            user_gender = profile.gender

    system_default = Avatar.objects.filter(
        is_default=True,
        gender=user_gender,
    ).first()

    if not system_default:
        system_default = Avatar.objects.filter(is_default=True).first()

    if not system_default:
        system_default = Avatar.objects.create(
            user=None,
            is_default=True,
            gender=user_gender,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_avatar.png",
        )

    return system_default


def generate_outfit_reasoning(user, items: list, scheduled_date=None) -> dict:
    """
    Generates AI reasoning for a given outfit (set of WardrobeItem objects) and user profile.
    Used for BOTH automatic and manual outfit generation.
    Returns a dictionary with:
    - title (str)
    - subtitle (str)
    - reasons (list of dicts with 'title' and 'description')
    - style_note (str)
    """
    weekday_name = scheduled_date.strftime("%A") if scheduled_date else timezone.now().strftime("%A")

    fallback_reasoning = {
        "title": "Daily Outfit Recommendation",
        "subtitle": "Smart automated outfit combination",
        "reasons": [
            {
                "title": "Style & Color Harmony",
                "description": "Selected garments create a cohesive and well-proportioned aesthetic."
            }
        ],
        "style_note": f"A balanced look tailored for your profile and {weekday_name}."
    }

    if not items:
        return fallback_reasoning

    profile_constraints = get_profile_constraints(user)
    gender = profile_constraints.get("gender") or "unspecified"
    aesthetics = profile_constraints.get("aesthetics") or []
    country = profile_constraints.get("country") or "unspecified"
    age = profile_constraints.get("age") or "unspecified"
    body_type = profile_constraints.get("body_type") or "unspecified"
    height = profile_constraints.get("height") or "unspecified"
    estimated_season = get_estimated_season(scheduled_date or timezone.now().date(), country)

    item_descriptions = []
    for item in items:
        sem = analyze_garment_semantics(item)
        item_descriptions.append({
            "id": item.id,
            "category": sem["category_name"],
            "color": sem["color"] or "unspecified",
            "description": sem["description"] or "",
        })

    api_key = getattr(settings, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    model_name = getattr(settings, "OPENAI_MODEL", "gpt-5.1") or os.getenv("OPENAI_MODEL", "gpt-5.1")

    if not api_key:
        logger.info("[OUTFIT REASONING] OPENAI_API_KEY is not set. Returning fallback reasoning.")
        return fallback_reasoning

    try:
        client = OpenAI(api_key=api_key)
        system_prompt = (
            "You are an expert AI fashion stylist for Fashion Hub AI (Hdoomi).\n"
            "Your goal is to provide concise, elegant, and insightful reasoning explaining why the provided outfit combination works well for the user.\n\n"
            "STRICT RULES:\n"
            "1. Base your reasoning on the actual items in the outfit and user profile.\n"
            "2. Return strictly a JSON object matching this schema:\n"
            "{\n"
            '  "title": "Catchy title for the look",\n'
            '  "subtitle": "Short subtitle summarizing the aesthetic",\n'
            '  "reasons": [\n'
            '    {"title": "Reason Title", "description": "Clear explanation of style/color/fit harmony"}\n'
            '  ],\n'
            '  "style_note": "A practical styling tip"\n'
            "}"
        )

        user_prompt = f"""
User Profile:
- Gender: {gender}
- Aesthetics: {json.dumps(aesthetics)}
- Age: {age}
- Country: {country}
- Body Type: {body_type}
- Height: {height}

Context:
- Day: {weekday_name}
- Season: {estimated_season}

Outfit Items:
{json.dumps(item_descriptions, indent=2)}

Return strictly a JSON object with title, subtitle, reasons (array of {{title, description}}), and style_note.
"""

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)

        title = str(parsed.get("title") or fallback_reasoning["title"])
        subtitle = str(parsed.get("subtitle") or fallback_reasoning["subtitle"])
        reasons = parsed.get("reasons")
        if not isinstance(reasons, list) or not reasons:
            reasons = fallback_reasoning["reasons"]
        else:
            clean_reasons = []
            for r in reasons:
                if isinstance(r, dict) and "title" in r and "description" in r:
                    clean_reasons.append({"title": str(r["title"]), "description": str(r["description"])})
            reasons = clean_reasons or fallback_reasoning["reasons"]

        style_note = str(parsed.get("style_note") or fallback_reasoning["style_note"])

        return {
            "title": title,
            "subtitle": subtitle,
            "reasons": reasons,
            "style_note": style_note,
        }
    except Exception as exc:
        logger.error("[OUTFIT REASONING] OpenAI reasoning generation failed: %s. Using fallback.", exc)
        return fallback_reasoning


def select_auto_tryon_assets(user, scheduled_date):
    """
    Fallback asset selector when OpenAI selection is bypassed or unavailable.
    - Selects completed avatar following priority chain.
    - Applies centralized gender & weather eligibility filtering.
    - Dynamically builds the best possible outfit composition from available items (does NOT require all categories).
    - Validates both semantic suitability and structural non-contradiction.
    """
    avatar = resolve_auto_daily_avatar(user)
    profile_constraints = get_profile_constraints(user)

    past_dates = [scheduled_date - timedelta(days=i) for i in range(1, 4)]
    recent_auto_jobs = OutfitJob.objects.filter(
        user=user,
        trigger_type=TriggerType.AUTO,
        scheduled_date__in=past_dates,
    ).prefetch_related("wardrobe_items")

    recent_item_ids = set()
    for past_job in recent_auto_jobs:
        recent_item_ids.update(past_job.wardrobe_items.values_list("id", flat=True))

    all_completed = get_user_usable_wardrobe_items(user)
    if not all_completed:
        return avatar, []

    eligible_items = filter_eligible_wardrobe_items(all_completed, profile_constraints, scheduled_date)
    if not eligible_items:
        return avatar, []

    candidates_by_group = {
        "full_body": [],
        "top": [],
        "bottom": [],
        "outerwear": [],
        "shoes": [],
        "accessory": [],
    }

    for item in eligible_items:
        s = analyze_garment_semantics(item)
        group = s["group"]
        if group in candidates_by_group:
            candidates_by_group[group].append(item)

    def pick_item(candidates):
        non_recent = [it for it in candidates if it.id not in recent_item_ids]
        return non_recent[0] if non_recent else candidates[0]

    selected_items = []

    # Dynamic selection strategy: Maximize completeness based ONLY on available items
    if candidates_by_group["full_body"]:
        selected_items.append(pick_item(candidates_by_group["full_body"]))
        if candidates_by_group["outerwear"]:
            selected_items.append(pick_item(candidates_by_group["outerwear"]))
        if candidates_by_group["shoes"]:
            selected_items.append(pick_item(candidates_by_group["shoes"]))
        if candidates_by_group["accessory"]:
            selected_items.append(pick_item(candidates_by_group["accessory"]))
    else:
        if candidates_by_group["top"]:
            selected_items.append(pick_item(candidates_by_group["top"]))
        if candidates_by_group["bottom"]:
            selected_items.append(pick_item(candidates_by_group["bottom"]))
        if candidates_by_group["outerwear"]:
            selected_items.append(pick_item(candidates_by_group["outerwear"]))
        if candidates_by_group["shoes"]:
            selected_items.append(pick_item(candidates_by_group["shoes"]))
        if candidates_by_group["accessory"]:
            selected_items.append(pick_item(candidates_by_group["accessory"]))

    if not selected_items and eligible_items:
        # Ultimate fallback: pick 1 item per available category from eligible items
        seen_cats = set()
        for item in eligible_items:
            if item.category_id not in seen_cats:
                seen_cats.add(item.category_id)
                selected_items.append(item)

    is_sem_valid, _ = validate_outfit_semantic_suitability(selected_items, profile_constraints, scheduled_date)
    is_comp_valid, _ = validate_outfit_composition(selected_items)
    if (not is_sem_valid or not is_comp_valid) and len(selected_items) > 1:
        # Fall back to single top/bottom/full_body if full combination had structural contradictions
        selected_items = [selected_items[0]]

    return avatar, selected_items


def select_auto_outfit_combination(user, scheduled_date):
    """
    OpenAI-powered wardrobe combination selector for daily AUTO outfits.
    Provides OpenAI with user profile (gender, aesthetics, age, country),
    date/season/occasion context, and eligible wardrobe items.
    Validates selection against centralized gender eligibility and composition rules.
    """
    profile_constraints = get_profile_constraints(user)
    avatar = resolve_auto_daily_avatar(user)

    all_completed = get_user_usable_wardrobe_items(user)
    if not all_completed:
        logger.warning("[AUTO OUTFIT] User %s has 0 eligible completed wardrobe items.", user.id if user else "anonymous")
        return avatar, [], generate_outfit_reasoning(user, [], scheduled_date)

    eligible_items = filter_eligible_wardrobe_items(all_completed, profile_constraints, scheduled_date)
    if not eligible_items:
        logger.warning("[AUTO OUTFIT] User %s has 0 eligible completed wardrobe items.", user.id if user else "anonymous")
        return avatar, [], generate_outfit_reasoning(user, [], scheduled_date)

    candidate_map = {item.id: item for item in eligible_items}
    candidate_data = []
    for item in eligible_items:
        sem = analyze_garment_semantics(item)
        candidate_data.append({
            "id": item.id,
            "category": sem["category_name"],
            "structural_group": sem["group"],
            "gender_presentation": sem["gender_presentation"],
            "season": getattr(item, "season", "") or "any",
            "occasion": getattr(item, "occasion", "") or "any",
            "color": sem["color"] or "unknown",
            "description": sem["description"] or "",
        })

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
    gender = profile_constraints.get("gender") or "unspecified"
    aesthetics = profile_constraints.get("aesthetics") or []
    country = profile_constraints.get("country") or "unspecified"
    age = profile_constraints.get("age") or "unspecified"
    body_type = profile_constraints.get("body_type") or "unspecified"
    height = profile_constraints.get("height") or "unspecified"
    estimated_season = get_estimated_season(scheduled_date, country)

    api_key = getattr(settings, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    model_name = getattr(settings, "OPENAI_MODEL", "gpt-5.1") or os.getenv("OPENAI_MODEL", "gpt-5.1")

    if not api_key:
        logger.warning("[AUTO OUTFIT] OPENAI_API_KEY is not set. Falling back to default selection.")
        _, fallback_items = select_auto_tryon_assets(user, scheduled_date)
        reasoning_dict = generate_outfit_reasoning(user, fallback_items, scheduled_date)
        return avatar, fallback_items, reasoning_dict

    try:
        logger.info("[AUTO OUTFIT] OpenAI selection started using model '%s' for gender=%s...", model_name, gender)
        client = OpenAI(api_key=api_key)
        system_prompt = (
            "You are an expert AI fashion stylist for Fashion Hub AI (Hdoomi).\n"
            "Your goal is to select the optimal wardrobe combination for today's daily AUTO outfit for the user.\n\n"
            "STRICT SELECTION & COMPOSITION RULES:\n"
            "1. Select item IDs ONLY from the provided candidate list. Never invent or hallucinate item IDs.\n"
            "2. Select MAXIMUM 1 item per category.\n"
            "3. Respect the user's Gender, Aesthetics, Age, Country, Body Type, Date, and Occasion.\n"
            "4. Male users MUST NEVER be assigned garments with 'female' gender presentation (e.g. dresses, feminine kaftans with draped bodices, skirts, abayas, borkhas).\n"
            "5. Heavy outerwear (e.g., trench coats, heavy overcoats) MUST NOT be paired with hot summer weather contexts (e.g. Bangladesh in August).\n"
            "6. OUTFIT STRUCTURE RULES:\n"
            "   - Select the best available combination of items from the provided candidate list.\n"
            "   - If only 1 item or incomplete categories are available (e.g. shirt only, shoes only, top + shoes), select those available items! Never fail or return empty selected items when candidate items exist.\n"
            "   - Maximum 1 item per category (e.g., 1 Top, 1 Bottom, 1 Full-body, 1 Shoes, 1 Outerwear, 1 Accessory).\n"
            "   - NEVER combine a Full-body garment with separate pants or tops.\n"
            "7. Avoid repeating the exact combination of item IDs used in any of the previous 3 days if alternatives exist.\n"
            "8. Return strictly a JSON object adhering to the JSON schema."
        )

        user_prompt = f"""
User Profile:
- Gender: {gender}
- Aesthetics: {json.dumps(aesthetics)}
- Age: {age}
- Country: {country}
- Body Type: {body_type}
- Height: {height} cm

Context:
- Today's Date: {date_str} ({weekday_name})
- Estimated Season: {estimated_season}


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
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)
    except Exception as exc:
        logger.error("[AUTO OUTFIT] OpenAI API call failed: %s. Using fallback asset selector.", exc)
        _, fallback_items = select_auto_tryon_assets(user, scheduled_date)
        reasoning_dict = generate_outfit_reasoning(user, fallback_items, scheduled_date)
        return avatar, fallback_items, reasoning_dict

    # --- 10-POINT BACKEND VALIDATION ---
    selected_ids = parsed.get("selected_item_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        logger.warning("[AUTO OUTFIT] OpenAI response invalid or empty. Falling back.")
        _, fallback_items = select_auto_tryon_assets(user, scheduled_date)
        reasoning_dict = generate_outfit_reasoning(user, fallback_items, scheduled_date)
        return avatar, fallback_items, reasoning_dict

    selected_items = []
    for item_id in selected_ids:
        if not isinstance(item_id, int):
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                continue

        if item_id in candidate_map:
            selected_items.append(candidate_map[item_id])

    selected_items = filter_eligible_wardrobe_items(selected_items, profile_constraints, scheduled_date)

    is_sem_valid, sem_reason = validate_outfit_semantic_suitability(selected_items, profile_constraints, scheduled_date)
    is_comp_valid, comp_reason = validate_outfit_composition(selected_items)
    if not is_sem_valid or not is_comp_valid:
        logger.warning(
            "[AUTO OUTFIT] OpenAI selected invalid recommendation (Semantic: %s | Comp: %s). Falling back to safe composition.",
            sem_reason,
            comp_reason,
        )
        _, fallback_items = select_auto_tryon_assets(user, scheduled_date)
        selected_items = fallback_items

    reasoning = parsed.get("reasoning", {})
    if not isinstance(reasoning, dict) or not reasoning.get("reasons"):
        reasoning_dict = generate_outfit_reasoning(user, selected_items, scheduled_date)
    else:
        reasoning_dict = {
            "title": str(reasoning.get("title", f"Daily Look for {weekday_name}")),
            "subtitle": str(reasoning.get("subtitle", "Curated AI Outfit")),
            "reasons": reasoning.get("reasons", []),
            "style_note": str(reasoning.get("style_note", "")),
        }

    return avatar, selected_items, reasoning_dict


def get_or_create_today_auto_job(user) -> OutfitJob:
    """
    Retrieves or lazily creates today's AUTO OutfitJob for the given user.
    Uses transaction.atomic() + unique constraint handling.
    OpenAI selection and fal.ai generation are performed strictly OUTSIDE database transactions.
    """
    today_date = timezone.now().date()

    logger.info("[AUTO OUTFIT] GET /today/ request for user %s on date %s", user.id, today_date)

    # 1. Read check: If today's non-failed AUTO job already exists, return it (NO OpenAI call, NO fal.ai call, NO cost!)
    existing_job = OutfitJob.objects.filter(
        user=user,
        scheduled_date=today_date,
        trigger_type=TriggerType.AUTO,
    ).first()

    if existing_job:
        if existing_job.status != JobStatus.FAILED:
            logger.info("[AUTO OUTFIT] Existing active AUTO job found (ID %s, status=%s). Returning cached job.", existing_job.id, existing_job.status)
            return existing_job
        else:
            logger.info("[AUTO OUTFIT] Existing AUTO job (ID %s) has status=FAILED. Retrying generation using current wardrobe state.", existing_job.id)
            existing_job.delete()

    logger.info("[AUTO OUTFIT] No active AUTO job found for user %s on date %s. Initiating generation.", user.id, today_date)

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
        avatar = resolve_auto_daily_avatar(user)
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
        avatar = avatar or resolve_auto_daily_avatar(user)
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

            if job and job.status == JobStatus.FAILED:
                job.delete()
                job = None

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


