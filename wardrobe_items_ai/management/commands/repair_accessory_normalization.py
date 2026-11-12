import io
import logging
from PIL import Image
import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from outfits.services import classify_accessory_subtype
from wardrobe_items_ai.models import ItemAnalysis, JobStatus
from wardrobe_items_ai.services import normalize_accessory_image

logger = logging.getLogger(__name__)


def is_accessory_image_normalized(image_bytes: bytes) -> bool:
    """
    Checks whether transparent image bytes are already normalized:
    - 1024x1024 canvas
    - RGBA mode with alpha channel
    - Non-empty alpha bounding box centered within delta of (512, 512)
    """
    if not image_bytes:
        return False
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.size != (1024, 1024):
            return False
        if "A" not in img.getbands():
            return False
        alpha = img.getchannel("A")
        bbox = alpha.getbbox()
        if not bbox:
            return False
        center_x = (bbox[0] + bbox[2]) / 2.0
        center_y = (bbox[1] + bbox[3]) / 2.0
        return abs(center_x - 512.0) <= 15.0 and abs(center_y - 512.0) <= 15.0
    except Exception:
        return False


class Command(BaseCommand):
    help = (
        "Idempotent and safe production repair command to audit historical saved accessories, "
        "normalize their processed images (alpha bounding-box crop, centered on 1024x1024 canvas with "
        "balanced padding), and upload to Cloudinary without modifying clothing items, original uploads, "
        "or database relationships."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the repair and display proposed changes without external network requests or database writes.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Filter repair to items belonging to a specific user ID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user_id = options.get("user_id")

        self.stdout.write(self.style.MIGRATE_HEADING("=== Historical Accessory Normalization & Repair ==="))
        if dry_run:
            self.stdout.write(
                self.style.WARNING("Mode: DRY-RUN (0 database writes, 0 external network requests)\n")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Mode: LIVE EXECUTION (with live image verification & normalization)\n"))

        queryset = (
            ItemAnalysis.objects.filter(status=JobStatus.DONE, is_saved=True)
            .select_related("wardrobe_item", "wardrobe_item__category")
            .order_by("id")
        )

        if user_id is not None:
            queryset = queryset.filter(wardrobe_item__user_id=user_id)
            self.stdout.write(f"Filtering to user ID: {user_id}")

        total_count = queryset.count()
        self.stdout.write(f"Total saved completed wardrobe records to inspect: {total_count}\n")

        # Distinct report counters
        repaired_count = 0
        already_normalized_count = 0
        skipped_clothing_count = 0
        skipped_no_source_count = 0
        skipped_invalid_count = 0
        failures_count = 0

        for analysis in queryset:
            try:
                item = analysis.wardrobe_item
                cat_name = getattr(item.category, "name", "") if (item and item.category) else ""
                desc = analysis.description or ""

                # 1. Scope: Only process items classified as accessories
                subtype = classify_accessory_subtype(cat_name, desc)
                if subtype is None:
                    skipped_clothing_count += 1
                    continue

                # 2. Check source URL
                source_url = ""
                if analysis.processed_image:
                    try:
                        source_url = analysis.processed_image.url
                    except Exception:
                        source_url = ""
                if not source_url and analysis.fal_cdn_url:
                    source_url = analysis.fal_cdn_url

                if not source_url or not str(source_url).strip().startswith(("http://", "https://")):
                    skipped_no_source_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [SKIPPED - NO SOURCE] Item {item.id} (user {item.user_id}, category '{cat_name}'): "
                            f"No valid image URL found."
                        )
                    )
                    continue

                # 3. Dry-run handling: do not make external HTTP requests or write to DB
                if dry_run:
                    repaired_count += 1
                    self.stdout.write(
                        f"  [DRY-RUN WOULD AUDIT/REPAIR] Item {item.id} (user {item.user_id}, category '{cat_name}', "
                        f"subtype '{subtype}'): Source URL={source_url}"
                    )
                    continue

                # 4. LIVE MODE: Download source image outside any database lock
                try:
                    img_resp = requests.get(source_url, timeout=30)
                    img_resp.raise_for_status()
                    raw_bytes = img_resp.content
                except Exception as dl_err:
                    failures_count += 1
                    logger.error("Download failed for wardrobe item %s from %s: %s", item.id, source_url, dl_err)
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [FAILED DOWNLOAD] Item {item.id} (user {item.user_id}): {dl_err}"
                        )
                    )
                    continue

                # 5. Check if image is already normalized
                if is_accessory_image_normalized(raw_bytes):
                    already_normalized_count += 1
                    self.stdout.write(
                        f"  [ALREADY NORMALIZED] Item {item.id} (user {item.user_id}, category '{cat_name}'): "
                        f"already centered on 1024x1024 canvas."
                    )
                    continue

                # 6. Apply normalization
                normalized_bytes = normalize_accessory_image(raw_bytes, cat_name, desc)
                if normalized_bytes == raw_bytes:
                    skipped_invalid_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [SKIPPED - INVALID] Item {item.id} (user {item.user_id}): "
                            f"normalization returned raw bytes (e.g. fully transparent or invalid alpha)."
                        )
                    )
                    continue

                # 7. Upload to Cloudinary under row lock and save
                with transaction.atomic():
                    locked_analysis = (
                        ItemAnalysis.objects.select_for_update()
                        .filter(pk=analysis.pk)
                        .first()
                    )
                    if not locked_analysis:
                        continue

                    filename = f"processed_item_{locked_analysis.wardrobe_item_id}.png"
                    locked_analysis.processed_image.save(filename, ContentFile(normalized_bytes), save=False)
                    locked_analysis.is_saved = True
                    locked_analysis.save(update_fields=["processed_image", "is_saved", "updated_at"])

                repaired_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [REPAIRED] Item {item.id} (user {item.user_id}, category '{cat_name}', subtype '{subtype}'): "
                        f"successfully normalized to 1024x1024 and saved to Cloudinary."
                    )
                )

            except Exception as exc:
                failures_count += 1
                logger.error("Unexpected error processing wardrobe item %s: %s", getattr(analysis, "wardrobe_item_id", None), exc, exc_info=True)
                self.stdout.write(
                    self.style.ERROR(
                        f"  [FAILED] Item {getattr(analysis, 'wardrobe_item_id', 'unknown')}: {exc}"
                    )
                )

        # Final Summary
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Repair Summary ==="))
        self.stdout.write(f"Total inspected:              {total_count}")
        self.stdout.write(f"Clothing items (skipped):     {skipped_clothing_count}")
        self.stdout.write(f"Already normalized:           {already_normalized_count}")
        if dry_run:
            self.stdout.write(f"Would normalize/repair:       {repaired_count}")
        else:
            self.stdout.write(f"Successfully repaired:        {repaired_count}")
        self.stdout.write(f"Skipped (no source URL):      {skipped_no_source_count}")
        self.stdout.write(f"Skipped (invalid image):      {skipped_invalid_count}")
        self.stdout.write(f"Failures / Errors:            {failures_count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\nDry-run complete. No database changes or uploads were performed.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("\nRepair execution completed successfully.")
            )

