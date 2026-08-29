import base64
import hashlib
import json
import logging
import re
import time
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
import fal_client
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
import requests
from .models import ItemAnalysis, JobStatus

logger = logging.getLogger(__name__)

FAL_BG_REMOVAL_MODEL_ID = "fal-ai/birefnet"
FAL_VISION_MODEL_ID = "nvidia/nemotron-3-nano-omni/vision"

JWKS_URL = "https://rest.fal.ai/.well-known/jwks.json"
JWKS_CACHE_DURATION = 24 * 60 * 60
_jwks_cache = None
_jwks_cache_time = 0


def fetch_jwks() -> list:
    global _jwks_cache, _jwks_cache_time
    current_time = time.time()
    if _jwks_cache is None or (current_time - _jwks_cache_time) > JWKS_CACHE_DURATION:
        response = requests.get(JWKS_URL, timeout=10)
        response.raise_for_status()
        _jwks_cache = response.json().get("keys", [])
        _jwks_cache_time = current_time
    return _jwks_cache


def verify_webhook_signature(
    request_id: str,
    user_id: str,
    timestamp: str,
    signature_hex: str,
    body: bytes,
) -> bool:
    try:
        timestamp_int = int(timestamp)
        if abs(int(time.time()) - timestamp_int) > 300:
            return False
    except (ValueError, TypeError):
        return False
    message_to_verify = "\n".join([
        request_id or "",
        user_id or "",
        timestamp or "",
        hashlib.sha256(body or b"").hexdigest(),
    ]).encode("utf-8")
    try:
        signature_bytes = bytes.fromhex(signature_hex)
    except (ValueError, TypeError):
        return False
    try:
        public_keys_info = fetch_jwks()
    except Exception:
        return False
    for key_info in public_keys_info:
        try:
            public_key_bytes = base64.urlsafe_b64decode(key_info["x"])
            verify_key = VerifyKey(public_key_bytes.hex(), encoder=HexEncoder)
            verify_key.verify(message_to_verify, signature_bytes)
            return True
        except (BadSignatureError, Exception):
            continue
    return False


def _friendly_error_message(raw_error: str) -> str:
    err_lower = (raw_error or "").lower()
    if any(kw in err_lower for kw in ("balance", "locked", "billing", "credit")):
        return "The AI analysis service is currently unavailable. Please try again later."
    if any(kw in err_lower for kw in ("safety", "policy", "content", "nsfw")):
        return "The image could not be processed due to safety policies. Please try uploading a different photo."
    if "timeout" in err_lower:
        return "The request timed out while analyzing your item photo. Please try again."
    return "Failed to analyze wardrobe item. Please try again later."


def _parse_json_response(output_text: str) -> dict:
    if not output_text:
        return {}
    if isinstance(output_text, dict):
        return output_text
    match = re.search(r"\{.*\}", str(output_text), re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(output_text)
    except Exception:
        return {}


async def submit_bg_removal_job_async(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Kicks off Step 1 (Background removal) via fal.ai submit_async with webhook_url.
    Updates ItemAnalysis status to PROCESSING or FAILED immediately on error.
    """
    wardrobe_item_obj = await sync_to_async(lambda: analysis.wardrobe_item)()
    source_photo_url = getattr(wardrobe_item_obj, "image", None)
    if hasattr(source_photo_url, "url"):
        source_photo_url = source_photo_url.url
    else:
        source_photo_url = str(source_photo_url or "")

    if not source_photo_url:
        analysis.status = JobStatus.FAILED
        analysis.internal_error_detail = "Wardrobe item has no image URL."
        analysis.error_message = "Item image is missing. Please re-upload your item photo."
        await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
        return analysis

    base_url = (getattr(settings, "WEBHOOK_BASE_URL", "") or "").rstrip("/")
    webhook_url = f"{base_url}/api/v1/wardrobe-items-ai/webhook/bg-removal/"

    try:
        handle = await sync_to_async(fal_client.submit)(
            FAL_BG_REMOVAL_MODEL_ID,
            arguments={"image_url": source_photo_url},
            webhook_url=webhook_url,
        )
        analysis.fal_request_id_bg_removal = handle.request_id
        analysis.status = JobStatus.PROCESSING
        analysis.error_message = ""
        analysis.internal_error_detail = ""
        await analysis.asave(update_fields=["fal_request_id_bg_removal", "status", "error_message", "internal_error_detail", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Background removal submission failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
        analysis.status = JobStatus.FAILED
        analysis.internal_error_detail = raw_error
        analysis.error_message = _friendly_error_message(raw_error)
        await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return analysis


async def submit_vision_job_async(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Kicks off Step 2 (Vision Model & Category Consistency Check) via fal.ai submit_async.
    """
    wardrobe_item_obj = await sync_to_async(lambda: analysis.wardrobe_item)()
    category_obj = await sync_to_async(lambda: getattr(wardrobe_item_obj, "category", None))()
    category_name = getattr(category_obj, "name", "clothing item") if category_obj else "clothing item"
    category_term = category_name

    prompt = (
        f"Analyze this image of a clothing or wardrobe item.\n"
        f"1. Identify the PRIMARY wardrobe or clothing item visible in the image. Ignore the person's identity, body, face, pose, or background. Note: The garment may be worn by a person; being worn by a person must NOT cause rejection.\n"
        f"2. Determine the specific garment/item type using fashion semantics and set 'detected_item_type' to this specific item type (for example: 'shirt dress', 'maxi dress', 'trench coat', 'button-down shirt', 'running shoes').\n"
        f"3. Perform semantic category comparison against the target database category '{category_term}':\n"
        f"   - Set 'matches_category' to true if the detected item belongs to, is a subtype of, is a synonym of, or is semantically compatible with the category '{category_term}' (including legitimate sub-styles, regional/traditional variations, and visually equivalent forms).\n"
        f"   - Do NOT require exact word matching.\n"
        f"   - Set 'matches_category' to false ONLY if the detected item fundamentally belongs to a completely different clothing category or is a non-apparel object.\n"
        f"4. Extract the primary dominant color of the item into 'color'.\n"
        f"5. Provide a short 1-2 sentence description of the item's visual style and key features into 'description'.\n"
        f"Respond ONLY with a JSON object containing keys: 'matches_category' (boolean), 'detected_item_type' (string), 'color' (string), 'description' (string)."
    )


    base_url = (getattr(settings, "WEBHOOK_BASE_URL", "") or "").rstrip("/")
    webhook_url = f"{base_url}/api/v1/wardrobe-items-ai/webhook/vision/"

    try:
        handle = await sync_to_async(fal_client.submit)(
            FAL_VISION_MODEL_ID,
            arguments={
                "image_url": analysis.fal_cdn_url,
                "prompt": prompt,
            },
            webhook_url=webhook_url,
        )
        analysis.fal_request_id_vision = handle.request_id
        analysis.status = JobStatus.PROCESSING
        await analysis.asave(update_fields=["fal_request_id_vision", "status", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Vision submission failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
        analysis.status = JobStatus.FAILED
        analysis.internal_error_detail = raw_error
        analysis.error_message = _friendly_error_message(raw_error)
        await analysis.asave(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return analysis


def submit_analysis_job(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Sync wrapper around submit_bg_removal_job_async for synchronous code locations.
    """
    from asgiref.sync import async_to_sync
    return async_to_sync(submit_bg_removal_job_async)(analysis)


def sync_analysis_status(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Checks job status with fal.ai and updates DB accordingly.
    Handles two stages:
      1. Background removal: if fal_request_id_bg_removal is completed, extracts fal_cdn_url and triggers vision job.
      2. Vision analysis: if fal_request_id_vision is completed, parses vision output, updates color/description,
         and marks analysis as DONE (or FAILED on category mismatch).
    Idempotent: returns immediately if analysis is already DONE, FAILED, or has no request IDs.
    """
    if analysis.status in (JobStatus.DONE, JobStatus.FAILED):
        return analysis

    # Stage 1: Background removal polling (if vision request has not been submitted yet)
    if analysis.fal_request_id_bg_removal and not analysis.fal_request_id_vision:
        try:
            status_info = fal_client.status(FAL_BG_REMOVAL_MODEL_ID, analysis.fal_request_id_bg_removal)
            if isinstance(status_info, fal_client.Completed):
                if getattr(status_info, "error", None):
                    raise Exception(f"fal.ai error: {status_info.error}")

                res = fal_client.result(FAL_BG_REMOVAL_MODEL_ID, analysis.fal_request_id_bg_removal)
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
                    raise Exception(f"No image URL found in fal.ai result: {res}")

                analysis.fal_cdn_url = image_url
                analysis.save(update_fields=["fal_cdn_url", "updated_at"])

                # Submit Stage 2 (Vision) synchronously via async_to_sync
                from asgiref.sync import async_to_sync
                async_to_sync(submit_vision_job_async)(analysis)
                return analysis
        except Exception as exc:
            raw_error = str(exc)
            logger.error("Background removal sync failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
            analysis.status = JobStatus.FAILED
            analysis.internal_error_detail = raw_error
            analysis.error_message = _friendly_error_message(raw_error)
            analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return analysis

    # Stage 2: Vision analysis polling (if vision request has been submitted)
    if analysis.status == JobStatus.PROCESSING and analysis.fal_request_id_vision:
        try:
            status_info = fal_client.status(FAL_VISION_MODEL_ID, analysis.fal_request_id_vision)
            if isinstance(status_info, fal_client.Completed):
                if getattr(status_info, "error", None):
                    raise Exception(f"fal.ai error: {status_info.error}")

                res = fal_client.result(FAL_VISION_MODEL_ID, analysis.fal_request_id_vision)
                raw_output = ""
                if isinstance(res, dict):
                    raw_output = res.get("output") or res.get("text") or res.get("content") or str(res)
                else:
                    raw_output = str(res)

                parsed = _parse_json_response(raw_output)
                category_obj = getattr(analysis.wardrobe_item, "category", None)
                category_name = getattr(category_obj, "name", "clothing item") if category_obj else "clothing item"
                detected_item_type = str(parsed.get("detected_item_type", "")).strip()

                matches_category = parsed.get("matches_category", True)
                if not matches_category:
                    friendly_msg = f"We couldn't find a clear {category_name} in this photo — please upload a photo showing just the item."
                    logger.warning(
                        "Category mismatch during sync for ItemAnalysis %s (detected_item_type='%s'): %s",
                        analysis.id,
                        detected_item_type,
                        friendly_msg,
                    )
                    analysis.status = JobStatus.FAILED
                    analysis.internal_error_detail = (
                        f"Category mismatch: vision model reported image (detected item: '{detected_item_type or 'unknown'}') "
                        f"does not match category '{category_name}'."
                    )
                    analysis.error_message = friendly_msg
                    analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
                    return analysis

                analysis.color = str(parsed.get("color", "")).strip()
                analysis.description = str(parsed.get("description", "")).strip()
                analysis.status = JobStatus.DONE
                analysis.is_saved = False
                analysis.error_message = ""
                analysis.internal_error_detail = ""
                analysis.save(
                    update_fields=["color", "description", "status", "is_saved", "error_message", "internal_error_detail", "updated_at"]
                )
        except Exception as exc:
            raw_error = str(exc)
            logger.error("Vision sync failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
            analysis.status = JobStatus.FAILED
            analysis.internal_error_detail = raw_error
            analysis.error_message = _friendly_error_message(raw_error)
            analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])

    return analysis


def save_wardrobe_item_to_cloudinary(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Explicitly downloads the AI-processed background-removed image from fal.ai CDN URL and uploads it to Cloudinary storage.
    Idempotent: if already saved, returns immediately without re-uploading.
    """
    if analysis.is_saved:
        return analysis

    url_to_download = analysis.fal_cdn_url or (analysis.processed_image.url if analysis.processed_image else "")
    if not url_to_download:
        from core.exceptions import ServiceError
        raise ServiceError(
            detail="Wardrobe item does not have a valid processed image to save.",
            status_code=400,
        )

    try:
        img_resp = requests.get(url_to_download, timeout=30)
        img_resp.raise_for_status()

        filename = f"processed_item_{analysis.wardrobe_item_id}.png"
        analysis.processed_image.save(filename, ContentFile(img_resp.content), save=False)
        analysis.is_saved = True
        analysis.save(update_fields=["processed_image", "is_saved", "updated_at"])
    except Exception as exc:
        from core.exceptions import ServiceError
        if isinstance(exc, ServiceError):
            raise exc
        raw_error = str(exc)
        logger.error("ItemAnalysis %s save to Cloudinary failed: %s", analysis.id, raw_error, exc_info=True)
        raise ServiceError(
            detail="Failed to save processed wardrobe item image. Please try again later.",
            debug_detail=raw_error,
            status_code=502,
        )

    return analysis


