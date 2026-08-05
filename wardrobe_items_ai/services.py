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

    prompt = (
        f"Analyze this image of a clothing or wardrobe item.\n"
        f"1. Check if the image displays an item matching the category '{category_name}'. Set 'matches_category' to true if yes, false if it is a completely different object or wrong category.\n"
        f"2. Extract the primary dominant color of the item.\n"
        f"3. Provide a short 1-2 sentence description of the item's visual style and key features.\n"
        f"Respond ONLY with a JSON object containing keys: 'matches_category' (boolean), 'color' (string), 'description' (string)."
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
    Returns the analysis instance as-is (read-only status check).
    """
    return analysis
