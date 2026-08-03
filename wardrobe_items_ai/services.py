import json
import logging
import re
import fal_client
import requests
from django.core.files.base import ContentFile
from .models import ItemAnalysis

logger = logging.getLogger(__name__)

FAL_BG_REMOVAL_MODEL_ID = "fal-ai/pixelcut/background-removal"
FAL_VISION_MODEL_ID = "nvidia/nemotron-3-nano-omni/vision"


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


def process_item(analysis: ItemAnalysis) -> None:
    """
    Two-step pipeline for wardrobe item analysis:
    1. Background removal via fal.ai pixelcut model.
    2. Vision model extraction (color, description, category consistency check).
    """
    analysis.status = ItemAnalysis.JobStatus.PROCESSING
    analysis.save(update_fields=["status", "updated_at"])

    wardrobe_item = analysis.wardrobe_item
    source_photo_url = getattr(wardrobe_item, "image", None)
    if hasattr(source_photo_url, "url"):
        source_photo_url = source_photo_url.url
    else:
        source_photo_url = str(source_photo_url or "")

    if not source_photo_url:
        analysis.status = ItemAnalysis.JobStatus.FAILED
        analysis.internal_error_detail = "Wardrobe item has no image URL."
        analysis.error_message = "Item image is missing. Please re-upload your item photo."
        analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
        return

    # Step 1: Background removal
    try:
        handle = fal_client.submit(
            FAL_BG_REMOVAL_MODEL_ID,
            arguments={"image_url": source_photo_url},
        )
        analysis.fal_request_id_bg_removal = handle.request_id
        analysis.save(update_fields=["fal_request_id_bg_removal", "updated_at"])

        res = handle.get()
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
            raise Exception(f"No image URL returned from background removal: {res}")

        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()

        filename = f"processed_item_{wardrobe_item.pk}.png"
        analysis.processed_image.save(filename, ContentFile(img_resp.content), save=False)
        analysis.save(update_fields=["processed_image", "updated_at"])
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Background removal failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
        analysis.status = ItemAnalysis.JobStatus.FAILED
        analysis.internal_error_detail = raw_error
        analysis.error_message = _friendly_error_message(raw_error)
        analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
        return

    # Step 2: Vision Model & Category Consistency Check
    try:
        processed_url = analysis.processed_image.url if analysis.processed_image else ""
        category_obj = getattr(wardrobe_item, "category", None)
        category_name = (
            getattr(category_obj, "name", str(category_obj))
            if category_obj
            else "clothing item"
        )

        prompt = (
            f"Analyze this image of a clothing or wardrobe item.\n"
            f"1. Check if the image displays an item matching the category '{category_name}'. Set 'matches_category' to true if yes, false if it is a completely different object or wrong category.\n"
            f"2. Extract the primary dominant color of the item.\n"
            f"3. Provide a short 1-2 sentence description of the item's visual style and key features.\n"
            f"Respond ONLY with a JSON object containing keys: 'matches_category' (boolean), 'color' (string), 'description' (string)."
        )

        handle_vision = fal_client.submit(
            FAL_VISION_MODEL_ID,
            arguments={
                "image_url": processed_url,
                "prompt": prompt,
            },
        )
        analysis.fal_request_id_vision = handle_vision.request_id
        analysis.save(update_fields=["fal_request_id_vision", "updated_at"])

        vision_res = handle_vision.get()
        raw_output = ""
        if isinstance(vision_res, dict):
            raw_output = vision_res.get("output") or vision_res.get("text") or vision_res.get("content") or str(vision_res)
        else:
            raw_output = str(vision_res)

        parsed = _parse_json_response(raw_output)

        matches_category = parsed.get("matches_category", True)
        if not matches_category:
            friendly_msg = f"We couldn't find a clear {category_name} in this photo — please upload a photo showing just the item."
            analysis.status = ItemAnalysis.JobStatus.FAILED
            analysis.internal_error_detail = f"Category mismatch: vision model reported image does not match category '{category_name}'."
            analysis.error_message = friendly_msg
            analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])
            return

        analysis.color = parsed.get("color", "").strip()
        analysis.description = parsed.get("description", "").strip()
        analysis.status = ItemAnalysis.JobStatus.DONE
        analysis.error_message = ""
        analysis.internal_error_detail = ""
        analysis.save()
    except Exception as exc:
        raw_error = str(exc)
        logger.error("Vision extraction failed for ItemAnalysis %s: %s", analysis.id, raw_error, exc_info=True)
        analysis.status = ItemAnalysis.JobStatus.FAILED
        analysis.internal_error_detail = raw_error
        analysis.error_message = _friendly_error_message(raw_error)
        analysis.save(update_fields=["status", "internal_error_detail", "error_message", "updated_at"])


def submit_analysis_job(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Submits and executes analysis for the given ItemAnalysis instance.
    """
    process_item(analysis)
    return analysis


def sync_analysis_status(analysis: ItemAnalysis) -> ItemAnalysis:
    """
    Checks or executes status update for the given analysis instance.
    """
    if analysis.status in (ItemAnalysis.JobStatus.DONE, ItemAnalysis.JobStatus.FAILED):
        return analysis
    process_item(analysis)
    return analysis
