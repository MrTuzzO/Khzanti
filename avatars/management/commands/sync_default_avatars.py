import os
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
import cloudinary
import cloudinary.api
import cloudinary.uploader

from avatars.models import Avatar

DEFAULT_AVATAR_ASSETS = [
    {
        "gender": "male",
        "style": Avatar.Style.REALISTIC,
        "public_id": "media/avatars/result/system_default_male_realistic_zo4mva",
        "local_file": "static/avatars/defaults/system_default_male_realistic.png",
        "description": "Male Realistic System Default Avatar",
    },
    {
        "gender": "male",
        "style": Avatar.Style.CARTOON,
        "public_id": "media/avatars/result/system_default_male_cartoon_ecftdo",
        "local_file": "static/avatars/defaults/system_default_male_cartoon.png",
        "description": "Male Cartoon System Default Avatar",
    },
    {
        "gender": "female",
        "style": Avatar.Style.REALISTIC,
        "public_id": "media/avatars/result/system_default_female_realistic_wfq4cl",
        "local_file": "static/avatars/defaults/system_default_female_realistic.png",
        "description": "Female Realistic System Default Avatar",
    },
    {
        "gender": "female",
        "style": Avatar.Style.CARTOON,
        "public_id": "media/avatars/result/system_default_female_cartoon_uh7xk3",
        "local_file": "static/avatars/defaults/system_default_female_cartoon.png",
        "description": "Female Cartoon System Default Avatar",
    },
    {
        "gender": None,
        "style": None,
        "public_id": "media/avatars/result/default_avatar",
        "local_file": "static/avatars/defaults/default_avatar.png",
        "description": "Legacy Fallback Default Avatar",
    },
]


class Command(BaseCommand):
    help = "Verify, upload missing default avatar images to Cloudinary, and synchronize database records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Inspect status and display proposed changes without uploading assets or updating the database.",
        )
        parser.add_argument(
            "--verify-urls",
            action="store_true",
            default=True,
            help="Verify that delivery URLs resolve with HTTP 200.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        verify_urls = options.get("verify_urls", True)

        cloud_name = getattr(settings, "CLOUDINARY_STORAGE", {}).get("CLOUD_NAME") or os.getenv("CLOUDINARY_CLOUD_NAME")
        self.stdout.write(self.style.MIGRATE_HEADING("=== Default Avatar Cloudinary & Database Audit ==="))
        self.stdout.write(f"Configured Cloud Name: {cloud_name}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY-RUN mode. No changes will be applied."))

        # Step 1: Audit & upload Cloudinary assets
        self.stdout.write("\n1. Inspecting Cloudinary assets...")
        cloudinary_results = {}

        for asset in DEFAULT_AVATAR_ASSETS:
            pub_id = asset["public_id"]
            desc = asset["description"]
            local_rel_path = asset["local_file"]
            local_abs_path = os.path.join(settings.BASE_DIR, local_rel_path)

            exists_in_cloud = False
            cloud_url = None

            try:
                res = cloudinary.api.resource(pub_id)
                exists_in_cloud = True
                cloud_url = res.get("secure_url") or res.get("url")
                self.stdout.write(
                    self.style.SUCCESS(f"  [FOUND] {desc} (public_id: {pub_id})")
                )
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "not found" in err_str.lower():
                    self.stdout.write(
                        self.style.WARNING(f"  [MISSING] {desc} (public_id: {pub_id})")
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f"  [ERROR] {desc} check failed: {err_str}")
                    )

            if not exists_in_cloud:
                if not os.path.exists(local_abs_path):
                    self.stdout.write(
                        self.style.ERROR(f"    Local file not found: {local_abs_path}. Cannot upload.")
                    )
                elif dry_run:
                    self.stdout.write(
                        f"    [DRY-RUN] Would upload {local_rel_path} -> public_id: {pub_id}"
                    )
                else:
                    self.stdout.write(f"    Uploading {local_rel_path} -> public_id: {pub_id}...")
                    try:
                        upload_res = cloudinary.uploader.upload(
                            local_abs_path,
                            public_id=pub_id,
                            overwrite=False,
                            resource_type="image",
                        )
                        cloud_url = upload_res.get("secure_url") or upload_res.get("url")
                        exists_in_cloud = True
                        self.stdout.write(
                            self.style.SUCCESS(f"    Uploaded successfully: {cloud_url}")
                        )
                    except Exception as upload_exc:
                        self.stdout.write(
                            self.style.ERROR(f"    Upload failed: {upload_exc}")
                        )

            cloudinary_results[pub_id] = {
                "exists": exists_in_cloud,
                "url": cloud_url,
                "asset": asset,
            }

            # Optional URL verification
            if verify_urls and exists_in_cloud and cloud_url:
                try:
                    head_res = requests.get(cloud_url, timeout=10)
                    if head_res.status_code == 200:
                        self.stdout.write(f"    URL verified (HTTP 200): {cloud_url}")
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"    URL returned HTTP {head_res.status_code}: {cloud_url}"
                            )
                        )
                except Exception as req_exc:
                    self.stdout.write(
                        self.style.WARNING(f"    URL check failed ({req_exc}): {cloud_url}")
                    )

        # Step 2: Synchronize Database records
        self.stdout.write("\n2. Synchronizing Database Avatar records...")
        for asset in DEFAULT_AVATAR_ASSETS:
            gender = asset["gender"]
            style = asset["style"]
            if not gender or not style:
                continue

            pub_id = asset["public_id"]
            desc = asset["description"]

            # Query system default for this (gender, style)
            existing = Avatar.objects.filter(is_default=True, gender=gender, style=style).first()

            if existing:
                current_img = existing.result_image.name if existing.result_image else ""
                if current_img != pub_id:
                    self.stdout.write(
                        f"  Updating {desc} (ID {existing.id}): current='{current_img}' -> new='{pub_id}'"
                    )
                    if not dry_run:
                        existing.result_image = pub_id
                        existing.status = Avatar.JobStatus.DONE
                        existing.is_saved = True
                        existing.save(update_fields=["result_image", "status", "is_saved", "updated_at"])
                        self.stdout.write(self.style.SUCCESS(f"    Updated Avatar #{existing.id}"))
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f"  [OK] {desc} (ID {existing.id}) points to '{pub_id}'")
                    )
            else:
                self.stdout.write(f"  Missing system default in DB: {desc} ({gender}, {style})")
                if not dry_run:
                    created = Avatar.objects.create(
                        user=None,
                        is_default=True,
                        gender=gender,
                        style=style,
                        status=Avatar.JobStatus.DONE,
                        is_saved=True,
                        result_image=pub_id,
                    )
                    self.stdout.write(self.style.SUCCESS(f"    Created Avatar #{created.id}"))

        # Step 3: Check for any legacy/stale default_avatar.png references
        stale_records = Avatar.objects.filter(result_image__icontains="default_avatar.png")
        if stale_records.exists():
            self.stdout.write(f"\n3. Found {stale_records.count()} stale default_avatar.png record(s):")
            for r in stale_records:
                target_pub_id = "media/avatars/result/system_default_male_realistic_zo4mva"
                if r.gender == "male" and r.style == Avatar.Style.CARTOON:
                    target_pub_id = "media/avatars/result/system_default_male_cartoon_ecftdo"
                elif r.gender == "female" and r.style == Avatar.Style.CARTOON:
                    target_pub_id = "media/avatars/result/system_default_female_cartoon_uh7xk3"
                elif r.gender == "female" and r.style == Avatar.Style.REALISTIC:
                    target_pub_id = "media/avatars/result/system_default_female_realistic_wfq4cl"

                self.stdout.write(f"  Record #{r.id} (gender={r.gender}, style={r.style}) -> {target_pub_id}")
                if not dry_run:
                    r.result_image = target_pub_id
                    r.save(update_fields=["result_image", "updated_at"])
                    self.stdout.write(self.style.SUCCESS(f"    Updated Record #{r.id}"))
        else:
            self.stdout.write("\n3. No stale default_avatar.png database records found.")

        self.stdout.write(self.style.SUCCESS("\nDefault avatars synchronization complete."))
