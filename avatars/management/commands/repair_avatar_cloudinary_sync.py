import logging
import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from avatars.models import Avatar

logger = logging.getLogger(__name__)


def is_cloudinary_asset_accessible(avatar) -> bool:
    """
    Verifies that the avatar's result_image actually exists and is accessible.
    Performs a lightweight HTTP HEAD request (falling back to streaming GET).
    Strictly returns False if the asset is missing, returns non-200, or times out.
    """
    if not avatar.result_image:
        return False
    try:
        url = avatar.result_image.url
        if not url or not str(url).startswith(("http://", "https://")):
            return False
        resp = requests.head(url, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return True
        if resp.status_code == 405:  # Method Not Allowed for HEAD on some CDN endpoints
            with requests.get(url, stream=True, timeout=10) as r:
                return r.status_code == 200
        return False
    except Exception as exc:
        logger.warning(
            "Cloudinary verification failed for avatar %s (%s): %s",
            avatar.id,
            getattr(avatar.result_image, "name", ""),
            exc,
        )
        return False


class Command(BaseCommand):
    help = (
        "Idempotent and safe production repair command to audit non-default Avatar records, "
        "verify actual Cloudinary asset existence, recover inaccessible assets from fal_cdn_url, "
        "upload missing assets, backfill saved_at timestamps, and preserve chronological order "
        "without triggering Today's Look regeneration."
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
            help="Filter repair to Avatar records belonging to a specific user ID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user_id = options.get("user_id")

        self.stdout.write(self.style.MIGRATE_HEADING("=== Avatar Cloudinary Synchronization & Data Repair ==="))
        if dry_run:
            self.stdout.write(
                self.style.WARNING("Mode: DRY-RUN (0 database writes, 0 external network requests)\n")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Mode: LIVE EXECUTION (with live asset verification)\n"))

        queryset = Avatar.objects.filter(is_default=False).order_by("id")
        if user_id is not None:
            queryset = queryset.filter(user_id=user_id)
            self.stdout.write(f"Filtering to user ID: {user_id}")

        total_count = queryset.count()
        self.stdout.write(f"Total non-default Avatar records to inspect: {total_count}\n")

        # Distinct report counters
        verified_assets_count = 0
        repaired_assets_count = 0
        backfilled_timestamps_count = 0
        inaccessible_cloudinary_count = 0
        missing_fal_sources_count = 0
        skipped_failed_count = 0
        skipped_incomplete_count = 0
        failures_count = 0

        for avatar in queryset:
            try:
                # 1. Explicitly process only DONE avatars
                if avatar.status == Avatar.JobStatus.FAILED:
                    skipped_failed_count += 1
                    self.stdout.write(
                        f"  [SKIPPED - FAILED] Avatar {avatar.id} (user {avatar.user_id}): status is FAILED."
                    )
                    continue

                if avatar.status in (Avatar.JobStatus.PENDING, Avatar.JobStatus.PROCESSING):
                    skipped_incomplete_count += 1
                    self.stdout.write(
                        f"  [SKIPPED - INCOMPLETE] Avatar {avatar.id} (user {avatar.user_id}): status is {avatar.status}."
                    )
                    continue

                if avatar.status != Avatar.JobStatus.DONE:
                    skipped_incomplete_count += 1
                    self.stdout.write(
                        f"  [SKIPPED - NON-DONE] Avatar {avatar.id} (user {avatar.user_id}): status is {avatar.status}."
                    )
                    continue

                # 2. DRY-RUN MODE: Zero network requests, zero database writes
                if dry_run:
                    has_result_image = bool(avatar.result_image and str(avatar.result_image.name).strip())
                    is_marked_saved = avatar.is_saved and has_result_image
                    source_url = avatar.fal_cdn_url or ""
                    has_valid_source = bool(
                        source_url and str(source_url).strip().startswith(("http://", "https://"))
                    )

                    if is_marked_saved:
                        if not avatar.saved_at:
                            backfilled_timestamps_count += 1
                            effective_time = avatar.created_at or timezone.now()
                            self.stdout.write(
                                f"  [PROPOSED BACKFILL] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"saved on Cloudinary, will backfill saved_at={effective_time}."
                            )
                        else:
                            verified_assets_count += 1
                            self.stdout.write(
                                f"  [PROPOSED VERIFIED] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"already marked saved with result_image."
                            )
                    elif has_valid_source:
                        repaired_assets_count += 1
                        effective_saved_at = avatar.saved_at or avatar.created_at or timezone.now()
                        self.stdout.write(
                            self.style.NOTICE(
                                f"  [PROPOSED REPAIR] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"will download from {source_url}, upload to Cloudinary, "
                                f"set is_saved=True, and saved_at={effective_saved_at}."
                            )
                        )
                    else:
                        missing_fal_sources_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  [SKIPPED - NO SOURCE] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"not saved and no usable fal_cdn_url found."
                            )
                        )
                    continue

                # 3. LIVE MODE: Verify Cloudinary asset existence
                has_result_image = bool(avatar.result_image and str(avatar.result_image.name).strip())
                is_marked_saved = avatar.is_saved and has_result_image
                cloudinary_accessible = False

                if is_marked_saved:
                    cloudinary_accessible = is_cloudinary_asset_accessible(avatar)
                    if not cloudinary_accessible:
                        inaccessible_cloudinary_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  [INACCESSIBLE CLOUDINARY] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"result_image exists in DB but asset is not accessible on Cloudinary."
                            )
                        )

                # 4. If asset is already saved AND verified accessible on Cloudinary
                if is_marked_saved and cloudinary_accessible:
                    if not avatar.saved_at:
                        # Backfill saved_at under row lock
                        with transaction.atomic():
                            locked = Avatar.objects.select_for_update().filter(id=avatar.id).first()
                            if locked and not locked.saved_at:
                                locked.saved_at = locked.created_at or timezone.now()
                                locked.save(update_fields=["saved_at", "updated_at"])
                        backfilled_timestamps_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  [BACKFILLED] Avatar {avatar.id} (user {avatar.user_id}): "
                                f"asset verified, populated missing saved_at={avatar.created_at}."
                            )
                        )
                    else:
                        verified_assets_count += 1
                        self.stdout.write(
                            f"  [VERIFIED] Avatar {avatar.id} (user {avatar.user_id}): "
                            f"is_saved=True, Cloudinary asset accessible, saved_at={avatar.saved_at}."
                        )
                    continue

                # 5. Asset needs repair (either unsaved OR Cloudinary asset was inaccessible)
                source_url = avatar.fal_cdn_url or ""
                if not source_url or not str(source_url).strip() or not str(source_url).startswith(("http://", "https://")):
                    missing_fal_sources_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [MISSING FAL SOURCE] Avatar {avatar.id} (user {avatar.user_id}): "
                            f"needs repair but no valid fal_cdn_url available. Skipping."
                        )
                    )
                    continue

                # Download source image OUTSIDE the database lock to avoid holding rows locked
                try:
                    img_resp = requests.get(source_url, timeout=30)
                    img_resp.raise_for_status()
                    img_bytes = img_resp.content
                except Exception as dl_err:
                    failures_count += 1
                    logger.error("Download failed for avatar %s from %s: %s", avatar.id, source_url, dl_err)
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [FAILED DOWNLOAD] Avatar {avatar.id} (user {avatar.user_id}): {dl_err}"
                        )
                    )
                    continue

                # Acquire row lock and re-check latest DB state before upload/write
                with transaction.atomic():
                    locked_avatar = Avatar.objects.select_for_update().filter(id=avatar.id).first()
                    if not locked_avatar:
                        continue

                    # Re-check status inside lock
                    if locked_avatar.status != Avatar.JobStatus.DONE:
                        self.stdout.write(
                            f"  [SKIPPED CONCURRENT] Avatar {locked_avatar.id}: status changed to {locked_avatar.status}."
                        )
                        continue

                    # If this was an unsaved record initially, but another worker saved it during our download:
                    if not is_marked_saved and (locked_avatar.is_saved and locked_avatar.result_image):
                        if not locked_avatar.saved_at:
                            locked_avatar.saved_at = locked_avatar.created_at or timezone.now()
                            locked_avatar.save(update_fields=["saved_at", "updated_at"])
                            backfilled_timestamps_count += 1
                        verified_assets_count += 1
                        self.stdout.write(
                            f"  [ALREADY SAVED CONCURRENT] Avatar {locked_avatar.id} already saved by another process."
                        )
                        continue

                    # Safe chronological timestamp
                    effective_saved_at = locked_avatar.saved_at or locked_avatar.created_at or timezone.now()

                    # Save to Cloudinary via model storage
                    filename = f"avatar_{locked_avatar.id}.png"
                    locked_avatar.result_image.save(filename, ContentFile(img_bytes), save=False)
                    locked_avatar.is_saved = True
                    locked_avatar.saved_at = effective_saved_at
                    locked_avatar.save(update_fields=["result_image", "is_saved", "saved_at", "updated_at"])

                repaired_assets_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [REPAIRED] Avatar {avatar.id} (user {avatar.user_id}): "
                        f"uploaded to Cloudinary, is_saved=True, saved_at={effective_saved_at}."
                    )
                )

            except Exception as exc:
                failures_count += 1
                logger.error("Failed to repair avatar %s: %s", avatar.id, exc, exc_info=True)
                self.stdout.write(
                    self.style.ERROR(
                        f"  [FAILED] Avatar {avatar.id} (user {avatar.user_id}): error: {exc}"
                    )
                )

        # Print summary
        self.stdout.write("\n" + self.style.MIGRATE_HEADING("=== Repair Summary ==="))
        self.stdout.write(f"Total inspected:                  {total_count}")
        self.stdout.write(f"Verified assets:                  {verified_assets_count}")
        self.stdout.write(f"Repaired assets:                  {repaired_assets_count}")
        self.stdout.write(f"Backfilled timestamps:            {backfilled_timestamps_count}")
        self.stdout.write(f"Inaccessible Cloudinary assets:   {inaccessible_cloudinary_count}")
        self.stdout.write(f"Missing fal.ai sources:           {missing_fal_sources_count}")
        self.stdout.write(f"Skipped - Failed status:          {skipped_failed_count}")
        self.stdout.write(f"Skipped - Incomplete/In-flight:   {skipped_incomplete_count}")
        self.stdout.write(f"Failures during repair:           {failures_count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\nDry-run complete. Re-run without --dry-run to execute live repair.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("\nRepair complete.")
            )
