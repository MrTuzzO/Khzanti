from datetime import date, date as date_type, timedelta
import io
from unittest.mock import MagicMock, patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase


from avatars.models import Avatar
from outfits.models import DailyOutfitSelection, JobStatus, OutfitJob, SavedOutfit, TriggerType
from outfits.services import build_try_on_prompt
from wardrobe.models import Category
from wardrobe_items_ai.models import ItemAnalysis, WardrobeItem

User = get_user_model()


def generate_test_image_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="red")
    img.save(buf, format="PNG")
    return buf.getvalue()


VALID_PNG_BYTES = generate_test_image_bytes()

MOCK_CLOUDINARY_RESPONSE = {
    "public_id": "outfits/result/test_tryon",
    "version": 1234567890,
    "width": 100,
    "height": 100,
    "format": "png",
    "resource_type": "image",
    "created_at": "2026-08-17T00:00:00Z",
    "bytes": 68,
    "type": "upload",
    "url": "http://res.cloudinary.com/test/image/upload/v1234567890/test_tryon.png",
    "secure_url": "https://res.cloudinary.com/test/image/upload/v1234567890/test_tryon.png",
}


class TryOnPromptBuilderTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="promptuser@example.com",
            name="Prompt User",
            password="password123",
        )
        self.avatar_real = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar_real.png",
        )
        self.avatar_cartoon = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.CARTOON,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar_cartoon.png",
        )

        self.cat_top, _ = Category.objects.get_or_create(name="Tops")
        self.cat_bottom, _ = Category.objects.get_or_create(name="Bottoms")
        self.dummy_img = SimpleUploadedFile(
            name="item.png", content=VALID_PNG_BYTES, content_type="image/png"
        )
        self.item_top = WardrobeItem.objects.create(
            user=self.user, category=self.cat_top, image=self.dummy_img
        )
        ItemAnalysis.objects.create(
            wardrobe_item=self.item_top,
            status=ItemAnalysis.JobStatus.DONE,
            color="navy blue",
            description="cotton button-down shirt",
        )
        self.item_bottom = WardrobeItem.objects.create(
            user=self.user, category=self.cat_bottom, image=self.dummy_img
        )
        ItemAnalysis.objects.create(
            wardrobe_item=self.item_bottom,
            status=ItemAnalysis.JobStatus.DONE,
            color="beige",
            description="tailored pleated trousers",
        )

    def test_realistic_avatar_try_on_prompt_hierarchy_and_rules(self):
        items = [self.item_top]
        prompt = build_try_on_prompt(self.avatar_real, items)

        # 1. Authoritative Base Scene / World
        self.assertIn("IMAGE 1 — AVATAR CONTROLS THE AUTHORITATIVE VISUAL WORLD", prompt)
        self.assertIn("photorealistic human photograph", prompt)
        self.assertIn("STRICT PRESERVATION: Do NOT repaint, restyle, regenerate, beautify, or alter the avatar", prompt)

        # 2. Wardrobe References
        self.assertIn("WARDROBE IMAGES ARE AUTHORITATIVE GARMENT REFERENCES", prompt)
        self.assertIn("WHAT TO WEAR, NOT HOW TO RENDER", prompt)
        self.assertIn("RECONSTRUCT, DO NOT COPY DEFECTS", prompt)
        self.assertIn("STRICT GARMENT FIDELITY (NO SUBSTITUTION)", prompt)
        self.assertIn("dress ≠ shirt + pants", prompt)
        self.assertIn("Reference Tops (color: navy blue, description: cotton button-down shirt)", prompt)

        # 3. Natural 3D Garment Reconstruction & Anti-compositing
        self.assertIn("NATURAL 3D GARMENT RECONSTRUCTION (NO COMPOSITING / NO PASTING)", prompt)
        self.assertIn("NEVER treat the wardrobe image as a texture, cutout, sticker, pasted layer, or pixel source", prompt)

        # 4. Photorealistic Style Execution
        self.assertIn("STYLE EXECUTION — PHOTOREALISTIC RENDERING", prompt)
        self.assertIn("ZERO difference in rendering fidelity between the person's skin and the clothing", prompt)

        # 5. Quality Test
        self.assertIn("VISUAL COHERENCE & ULTIMATE QUALITY TEST", prompt)
        self.assertIn("It must NEVER look like someone took a wardrobe product image and photoshopped it onto the person", prompt)

    def test_cartoon_avatar_try_on_prompt(self):
        items = [self.item_top]
        prompt = build_try_on_prompt(self.avatar_cartoon, items)

        self.assertIn("IMAGE 1 — AVATAR CONTROLS THE AUTHORITATIVE VISUAL WORLD (cartoon / stylized / illustrated artwork)", prompt)
        self.assertIn("STYLE EXECUTION — CARTOON / ILLUSTRATED / STYLIZED RENDERING", prompt)
        self.assertIn("Any photorealistic wardrobe reference photo must be completely stylized and redrawn", prompt)
        self.assertIn("NEVER place a photorealistic garment or real photographic texture onto a cartoon avatar", prompt)

    def test_multi_garment_try_on_prompt_layering(self):
        items = [self.item_top, self.item_bottom]
        prompt = build_try_on_prompt(self.avatar_real, items)

        self.assertIn("Image 2: Reference Tops", prompt)
        self.assertIn("Image 3: Reference Bottoms", prompt)
        self.assertIn("MULTI-GARMENT PHYSICAL LAYERING", prompt)
        self.assertIn("shirts tucked into/under pants or jackets", prompt)


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class TryOnSaveAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tryonuser@example.com",
            name="TryOn User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.other_user = User.objects.create_user(
            email="otheruser@example.com",
            name="Other User",
            password="password123",
        )

        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar.png",
            is_saved=True,
        )

        self.category, _ = Category.objects.get_or_create(name="Tops")
        self.dummy_image = SimpleUploadedFile(
            name="top.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )
        self.wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image=self.dummy_image,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=self.wardrobe_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/top_bg.png",
            is_saved=False,
        )

    @patch("requests.get")
    def test_manual_tryon_save_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_tryon_result.png",
            is_saved=False,
        )
        job.wardrobe_items.add(self.wardrobe_item)

        # 1. Status check returns fal.ai URL & saved=False
        status_resp = self.client.get(f"/api/v1/outfits/try-on/{job.id}/status/")
        self.assertEqual(status_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp.data["result_image"], "https://v3b.fal.media/manual_tryon_result.png")

        # 2. Save endpoint uploads to Cloudinary
        save_resp = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(save_resp.data["saved"])
        self.assertIn("http", save_resp.data["result_image"])

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

        # 3. Duplicate save call is idempotent
        save_resp_2 = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp_2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_requests_get.call_count, 1)

    @patch("requests.get")
    def test_auto_tryon_save_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_tryon_result.png",
            is_saved=False,
        )
        job.wardrobe_items.add(self.wardrobe_item)

        # Save AUTO try-on
        save_resp = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(save_resp.data["saved"])
        self.assertEqual(save_resp.data["trigger_type"], TriggerType.AUTO)

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

    @patch("requests.get")
    def test_calendar_save_now_unsaved_manual_tryon(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_tryon_result.png",
            is_saved=False,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
            "note": "Work outfit",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["saved_date"], "2026-08-25")
        self.assertTrue(response.data["outfit_job"]["saved"])
        self.assertIn("http", response.data["outfit_job"]["result_image"])

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

    @patch("requests.get")
    def test_calendar_save_now_unsaved_auto_tryon(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_tryon_result.png",
            is_saved=False,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["outfit_job"]["saved"])
        mock_requests_get.assert_called_once()

    @patch("requests.get")
    def test_calendar_save_now_already_saved_tryon(self, mock_requests_get, mock_cloud):
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/already_saved.png",
            is_saved=True,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_requests_get.assert_not_called()

    @patch("requests.get")
    def test_generation_date_differs_from_calendar_date(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        from datetime import date
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 17),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/diff_dates.png",
            is_saved=False,
        )

        # Save for August 25
        self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        # Query August 25 calendar -> outfit present
        res_aug_25 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-25&end_date=2026-08-25")
        self.assertEqual(res_aug_25.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_aug_25.data), 1)

        # Query August 17 calendar -> 0 outfits
        res_aug_17 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-17&end_date=2026-08-17")
        self.assertEqual(res_aug_17.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_aug_17.data), 0)

    @patch("requests.get")
    def test_patch_saved_outfit_date(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        from datetime import date
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 17),
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/patch_test.png",
            is_saved=False,
        )

        post_res = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })
        saved_id = post_res.data["id"]

        # Update date to August 28
        patch_res = self.client.patch(f"/api/v1/outfits/saved/{saved_id}/", {
            "saved_date": "2026-08-28",
        })
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        # Verify disappears from August 25 and appears on August 28
        res_25 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-25&end_date=2026-08-25")
        self.assertEqual(len(res_25.data), 0)

        res_28 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-28&end_date=2026-08-28")
        self.assertEqual(len(res_28.data), 1)

        # Verify OutfitJob.scheduled_date remains unchanged
        job.refresh_from_db()
        self.assertEqual(job.scheduled_date, date(2026, 8, 17))

    def test_unauthorized_outfit_job_calendar_save(self, mock_cloud):
        other_job = OutfitJob.objects.create(
            user=self.other_user,
            avatar=self.avatar,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other.png",
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": other_job.id,
            "saved_date": "2026-08-25",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_incomplete_outfit_job_calendar_save_rejection(self, mock_cloud):
        pending_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            status=JobStatus.PENDING,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": pending_job.id,
            "saved_date": "2026-08-25",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("requests.get")
    def test_cloudinary_failure_prevents_saved_outfit_creation(self, mock_requests_get, mock_cloud):
        mock_requests_get.side_effect = Exception("Cloudinary upload failed")

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/fail.png",
            is_saved=False,
        )

        from outfits.models import SavedOutfit
        count_before = SavedOutfit.objects.filter(user=self.user).count()

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        count_after = SavedOutfit.objects.filter(user=self.user).count()
        self.assertEqual(count_before, count_after)

        job.refresh_from_db()
        self.assertFalse(job.is_saved)


class OneMonthOutfitHistoryAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user1month@example.com",
            name="1Month User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="other1month@example.com",
            name="Other User",
            password="password123",
        )
        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )

    def test_unauthenticated_request_rejected(self):
        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_history_returns_200_empty_list(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_authenticated_user_receives_outfits_within_last_30_days_and_excludes_older_and_other_users(self):
        self.client.force_authenticate(user=self.user)

        now = timezone.now()

        # Job 1: Recent job created 2 days ago
        recent_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=recent_job.pk).update(created_at=now - timedelta(days=2))

        # Job 2: Recent job created today
        today_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=today_job.pk).update(created_at=now)

        # Job 3: Old job created 35 days ago (older than current month)
        old_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=(now - timedelta(days=35)).date(),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=old_job.pk).update(created_at=now - timedelta(days=35))

        # Job 4: Other user's job created today
        other_user_job = OutfitJob.objects.create(
            user=self.other_user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=other_user_job.pk).update(created_at=now)

        # Create DailyOutfitSelection for recent_job and today_job
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=(now - timedelta(days=2)).date(),
            outfit_job=recent_job,
        )
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=now.date(),
            outfit_job=today_job,
        )
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=(now - timedelta(days=35)).date(),
            outfit_job=old_job,
        )
        DailyOutfitSelection.objects.create(
            user=self.other_user,
            date=now.date(),
            outfit_job=other_user_job,
        )

        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        retrieved_ids = [item["id"] for item in response.data]
        self.assertEqual(len(retrieved_ids), 2)
        self.assertCountEqual(retrieved_ids, [today_job.id, recent_job.id])
        self.assertNotIn(old_job.id, retrieved_ids)
        self.assertNotIn(other_user_job.id, retrieved_ids)

    def test_month_and_year_parameter_filtering_and_leap_year_support(self):
        self.client.force_authenticate(user=self.user)

        # Job in July 2026 (July 15, 2026)
        july_2026_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_type(2026, 7, 15),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )

        # Job in August 2026 (August 10, 2026)
        august_2026_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_type(2026, 8, 10),
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
        )

        # Job in Feb 2024 (Leap year - Feb 29, 2024)
        feb_leap_2024_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_type(2024, 2, 29),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )

        # Job in Feb 2026 (Non-leap year - Feb 28, 2026)
        feb_2026_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_type(2026, 2, 28),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )

        # Create DailyOutfitSelections
        DailyOutfitSelection.objects.create(user=self.user, date=date_type(2026, 7, 15), outfit_job=july_2026_job)
        DailyOutfitSelection.objects.create(user=self.user, date=date_type(2026, 8, 10), outfit_job=august_2026_job)
        DailyOutfitSelection.objects.create(user=self.user, date=date_type(2024, 2, 29), outfit_job=feb_leap_2024_job)
        DailyOutfitSelection.objects.create(user=self.user, date=date_type(2026, 2, 28), outfit_job=feb_2026_job)

        # 1. Query July 2026 (month=7&year=2026)
        res_july = self.client.get("/api/v1/outfits/1-months/?month=7&year=2026")
        self.assertEqual(res_july.status_code, status.HTTP_200_OK)
        july_ids = [item["id"] for item in res_july.data]
        self.assertEqual(july_ids, [july_2026_job.id])

        # 2. Query August 2026 (month=8&year=2026)
        res_august = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res_august.status_code, status.HTTP_200_OK)
        august_ids = [item["id"] for item in res_august.data]
        self.assertEqual(august_ids, [august_2026_job.id])

        # 3. Query Feb 2024 leap year (month=2&year=2024)
        res_feb_2024 = self.client.get("/api/v1/outfits/1-months/?month=2&year=2024")
        self.assertEqual(res_feb_2024.status_code, status.HTTP_200_OK)
        feb_2024_ids = [item["id"] for item in res_feb_2024.data]
        self.assertEqual(feb_2024_ids, [feb_leap_2024_job.id])

        # 4. Query Feb 2026 non-leap year (month=2&year=2026)
        res_feb_2026 = self.client.get("/api/v1/outfits/1-months/?month=2&year=2026")
        self.assertEqual(res_feb_2026.status_code, status.HTTP_200_OK)
        feb_2026_ids = [item["id"] for item in res_feb_2026.data]
        self.assertEqual(feb_2026_ids, [feb_2026_job.id])


import json
from django.conf import settings
from accounts.models import CustomerProfile, Gender, Aesthetic
from outfits.services import (
    filter_eligible_wardrobe_items,
    validate_outfit_composition,
    validate_outfit_semantic_suitability,
    analyze_garment_semantics,
    categorize_item_semantics,
    select_auto_tryon_assets,
    select_auto_outfit_combination,
    get_profile_constraints,
    resolve_tryon_avatar,
    get_or_create_today_auto_job,
)



@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class DailyOutfitPipelineRecommendationAPITestCase(APITestCase):
    def setUp(self):

        self.male_user = User.objects.create_user(
            email="maleuser@example.com",
            name="Male User",
            password="Password123!",
        )
        self.male_profile, _ = CustomerProfile.objects.get_or_create(
            user=self.male_user,
            defaults={
                "gender": Gender.MALE,
                "age": 25,
                "country": "Bangladesh",
                "body_type": "average",
                "height": 175,
            }
        )

        self.female_user = User.objects.create_user(
            email="femaleuser@example.com",
            name="Female User",
            password="Password123!",
        )
        self.female_profile, _ = CustomerProfile.objects.get_or_create(
            user=self.female_user,
            defaults={
                "gender": Gender.FEMALE,
                "age": 24,
                "country": "Bangladesh",
                "body_type": "slim",
                "height": 165,
            }
        )
        minimalist_aesthetic, _ = Aesthetic.objects.get_or_create(name="Minimalist")
        self.female_profile.aesthetics.add(minimalist_aesthetic)

        self.cat_tops, _ = Category.objects.get_or_create(name="Tops & T-Shirts")
        self.cat_bottoms, _ = Category.objects.get_or_create(name="Bottoms & Pants")
        self.cat_dresses, _ = Category.objects.get_or_create(name="Dresses")
        self.cat_kaftan, _ = Category.objects.get_or_create(name="Kaftan")
        self.cat_outerwear, _ = Category.objects.get_or_create(name="Outerwear")
        self.cat_shoes, _ = Category.objects.get_or_create(name="Shoes")

    def _create_item(self, user, category, description="", color=""):
        img = SimpleUploadedFile("item.jpg", b"image_data", content_type="image/jpeg")
        item = WardrobeItem.objects.create(
            user=user,
            category=category,
            image=img,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url=f"https://v3b.fal.media/item_{item.id}.png",
            description=description,
            color=color,
            is_saved=False,
        )
        return item


    def test_profile_constraints_resolved_from_customer_profile(self, *args):
        constraints = get_profile_constraints(self.female_user)
        self.assertEqual(constraints["gender"], "female")
        self.assertIn("Minimalist", constraints["aesthetics"])

    def test_male_user_eligible_filtering_excludes_dresses(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)

        all_items = [top, bottom, dress]
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items(all_items, constraints)

        self.assertIn(top, eligible)
        self.assertIn(bottom, eligible)
        self.assertNotIn(dress, eligible)

    def test_outfit_composition_validation_rules(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.female_user, self.cat_dresses)
        shoes = self._create_item(self.male_user, self.cat_shoes)

        # 1. Top + Bottom + Shoes -> Valid
        valid_2pc, _ = validate_outfit_composition([top, bottom, shoes])
        self.assertTrue(valid_2pc)

        # 2. Dress + Shoes -> Valid
        valid_dress, _ = validate_outfit_composition([dress, shoes])
        self.assertTrue(valid_dress)

        # 3. Dress + Bottom -> Invalid
        invalid_dress_pants, _ = validate_outfit_composition([dress, bottom])
        self.assertFalse(invalid_dress_pants)

        # 4. Top + Top -> Invalid
        invalid_top_top, _ = validate_outfit_composition([top, top])
        self.assertFalse(invalid_top_top)

        # 5. Shoes only -> Valid (AUTO must attempt generation with any available usable item)
        valid_shoes_only, _ = validate_outfit_composition([shoes])
        self.assertTrue(valid_shoes_only)

        # 6. Top alone -> Valid (Incomplete wardrobe support)
        valid_top_alone, _ = validate_outfit_composition([top])
        self.assertTrue(valid_top_alone)

        # 7. Bottom alone -> Valid (Incomplete wardrobe support)
        valid_bottom_alone, _ = validate_outfit_composition([bottom])
        self.assertTrue(valid_bottom_alone)

    def test_incomplete_wardrobe_top_alone_and_top_jacket_selection(self, *args):
        top = self._create_item(self.male_user, self.cat_tops, description="Blue Oxford shirt")
        today_date = timezone.now().date()

        # Single top in wardrobe -> Generates Top alone without error
        _, selected_items = select_auto_tryon_assets(self.male_user, today_date)
        self.assertEqual(selected_items, [top])

        # Top + Outerwear in wardrobe (no pants/shoes) -> Generates Top + Outerwear
        jacket = self._create_item(self.male_user, self.cat_outerwear, description="Light blue blazer")
        _, selected_items_2 = select_auto_tryon_assets(self.male_user, today_date)
        self.assertIn(top, selected_items_2)
        self.assertIn(jacket, selected_items_2)

    def test_incomplete_wardrobe_dress_alone_selection(self, *args):
        dress = self._create_item(self.female_user, self.cat_dresses, description="Floral summer dress")
        today_date = timezone.now().date()

        # Single dress in wardrobe -> Generates Dress alone without requiring shoes/accessories
        _, selected_items = select_auto_tryon_assets(self.female_user, today_date)
        self.assertEqual(selected_items, [dress])

    def test_ambiguous_traditional_garment_allowed(self, *args):
        ambiguous_kaftan = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="Traditional white kaftan garment.",
            color="white"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([ambiguous_kaftan], constraints)
        self.assertIn(ambiguous_kaftan, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([ambiguous_kaftan], constraints)
        self.assertTrue(is_sem_valid)


    def test_male_user_feminine_kaftan_dress_rejected_semantically(self, *args):
        kaftan_dress = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A long, flowing beige Kaftan dress with short sleeves and a draped bodice featuring a decorative brooch at the waist.",
            color="beige"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([kaftan_dress], constraints)
        self.assertNotIn(kaftan_dress, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([kaftan_dress], constraints)
        self.assertFalse(is_sem_valid)

    def test_male_user_men_traditional_thobe_allowed(self, *args):
        men_thobe = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A classic white men's thobe / kaftan with mandarin collar.",
            color="white"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([men_thobe], constraints)
        self.assertIn(men_thobe, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([men_thobe], constraints)
        self.assertTrue(is_sem_valid)

    def test_exact_outfit_job_198_scenario_rejection(self, *args):
        # Recreating exact scenario of OutfitJob 198 (feminine kaftan dress 78 + heavy trench coat 72 + white sneakers 21 for male in Bangladesh summer)
        kaftan_dress_78 = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A long, flowing beige Kaftan dress with short sleeves and a draped bodice featuring a decorative brooch at the waist.",
            color="beige"
        )
        trench_coat_72 = self._create_item(
            self.male_user,
            self.cat_outerwear,
            description="A classic beige trench coat with double-breasted buttons, epaulets, and a waist belt, worn over a suit.",
            color="beige"
        )
        sneakers_21 = self._create_item(
            self.male_user,
            self.cat_shoes,
            description="A white low-top sneaker featuring a classic design.",
            color="white"
        )

        constraints = get_profile_constraints(self.male_user)
        today_date = timezone.now().date()

        is_sem_valid, _ = validate_outfit_semantic_suitability([kaftan_dress_78, trench_coat_72, sneakers_21], constraints, today_date)
        self.assertFalse(is_sem_valid)

    def test_fallback_selection_does_not_blindly_pick_all_categories(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)
        shoes = self._create_item(self.male_user, self.cat_shoes)

        today_date = timezone.now().date()
        _, selected_items = select_auto_tryon_assets(self.male_user, today_date)

        self.assertNotIn(dress, selected_items)
        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)

    def test_female_user_can_select_dress_outfit(self, *args):
        dress = self._create_item(self.female_user, self.cat_dresses)
        shoes = self._create_item(self.female_user, self.cat_shoes)

        today_date = timezone.now().date()
        _, selected_items = select_auto_tryon_assets(self.female_user, today_date)

        self.assertIn(dress, selected_items)
        self.assertIn(shoes, selected_items)

    def test_fullbody_garment_plus_outerwear_valid_composition(self, *args):
        outerwear_cat, _ = Category.objects.get_or_create(name="Outerwear")
        dress = self._create_item(self.female_user, self.cat_dresses)
        jacket = self._create_item(self.female_user, outerwear_cat, description="Light stylish denim jacket")
        bottom = self._create_item(self.female_user, self.cat_bottoms)

        # Full-body + Outerwear -> Valid
        valid_fb_outer, _ = validate_outfit_composition([dress, jacket])
        self.assertTrue(valid_fb_outer)

        # Full-body + Bottom -> Invalid
        invalid_fb_bottom, _ = validate_outfit_composition([dress, bottom])
        self.assertFalse(invalid_fb_bottom)

    @patch("outfits.services.OpenAI")
    def test_openai_invalid_category_combination_rejected_by_backend(self, mock_openai, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "selected_item_ids": [top.id, bottom.id, dress.id],
                "reasoning": {"title": "Test Look", "subtitle": "Test"}
            })))
        ]

        today_date = timezone.now().date()
        with patch.object(settings, "OPENAI_API_KEY", "dummy_key"):
            _, selected_items, _ = select_auto_outfit_combination(self.male_user, today_date)

        self.assertNotIn(dress, selected_items)
        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)

    @patch("outfits.services.OpenAI")
    def test_openai_failure_uses_valid_fallback(self, mock_openai, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.side_effect = Exception("OpenAI service unavailable")

        today_date = timezone.now().date()
        with patch.object(settings, "OPENAI_API_KEY", "dummy_key"):
            _, selected_items, reasoning = select_auto_outfit_combination(self.male_user, today_date)

        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)
        self.assertIn("Daily Outfit Recommendation", reasoning["title"])

    def test_no_valid_composition_returns_failed_job_response(self, *args):
        # Male user only has a dress (0 eligible items)
        dress = self._create_item(self.male_user, self.cat_dresses)

        job = get_or_create_today_auto_job(self.male_user)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("No completed wardrobe items", job.error_message)

    @patch("outfits.services.submit_try_on_job")
    def test_today_outfit_api_endpoint_lazy_generation(self, mock_submit, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        avatar = resolve_tryon_avatar(self.male_user)

        self.client.force_authenticate(user=self.male_user)
        response = self.client.get("/api/v1/outfits/today/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "success")
        self.assertIn("id", response.data["data"])

    @patch("outfits.services.submit_try_on_job")
    def test_failed_today_auto_job_is_retried_on_get_today(self, mock_submit, *args):
        today_date = timezone.now().date()
        avatar = resolve_tryon_avatar(self.male_user)
        
        # 1. Create a stale failed job for today
        stale_failed_job = OutfitJob.objects.create(
            user=self.male_user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            error_message="Stale failure from earlier"
        )
        
        # 2. Add valid completed wardrobe items to user's wardrobe
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)

        # 3. GET /today/ should retry generation and create a new active job
        new_job = get_or_create_today_auto_job(self.male_user)
        self.assertNotEqual(new_job.id, stale_failed_job.id)
        self.assertNotEqual(new_job.status, JobStatus.FAILED)
        self.assertIn(top, new_job.wardrobe_items.all())
        self.assertFalse(OutfitJob.objects.filter(id=stale_failed_job.id).exists())

    @patch("outfits.services.submit_try_on_job")
    def test_done_today_auto_job_is_cached(self, mock_submit, *args):
        today_date = timezone.now().date()
        avatar = resolve_tryon_avatar(self.male_user)
        
        # 1. Create a DONE job for today
        done_job = OutfitJob.objects.create(
            user=self.male_user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            result_image="https://res.cloudinary.com/demo/image.jpg"
        )
        
        # 2. GET /today/ returns the cached DONE job without creating a new one
        retrieved_job = get_or_create_today_auto_job(self.male_user)
        self.assertEqual(retrieved_job.id, done_job.id)
        self.assertEqual(retrieved_job.status, JobStatus.DONE)


from outfits.models import SavedOutfit

@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class SavedOutfitWorkflowAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="saveduser@example.com",
            name="Saved Outfit User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="othersaveduser@example.com",
            name="Other Saved User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar.png",
            is_saved=True,
        )

        self.category, _ = Category.objects.get_or_create(name="Tops")
        self.dummy_image = SimpleUploadedFile(
            name="top.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )
        self.wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image=self.dummy_image,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=self.wardrobe_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/top_bg.png",
            is_saved=False,
        )

    def _mock_requests_get(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

    @patch("requests.get")
    def test_a_empty_saved_history(self, mock_get, mock_cloud):
        response = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    @patch("requests.get")
    def test_b_one_saved_automatic_outfit(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        today_date = timezone.now().date()
        auto_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_tryon_result.png",
            is_saved=False,
        )
        auto_job.wardrobe_items.add(self.wardrobe_item)

        save_resp = self.client.post(f"/api/v1/outfits/try-on/{auto_job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 1)
        self.assertEqual(saved_list[0]["outfit_job"]["id"], auto_job.id)
        self.assertEqual(saved_list[0]["outfit_job"]["trigger_type"], TriggerType.AUTO)

    @patch("requests.get")
    def test_c_one_saved_manual_outfit(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        today_date = timezone.now().date()
        manual_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_tryon_result.png",
            is_saved=False,
        )
        manual_job.wardrobe_items.add(self.wardrobe_item)

        save_resp = self.client.post(f"/api/v1/outfits/try-on/{manual_job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 1)
        self.assertEqual(saved_list[0]["outfit_job"]["id"], manual_job.id)
        self.assertEqual(saved_list[0]["outfit_job"]["trigger_type"], TriggerType.MANUAL)

    @patch("requests.get")
    def test_d_multiple_saved_outfits_and_same_day_manual_outfits(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        today_date = timezone.now().date()

        manual_1 = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual1.png",
        )
        manual_2 = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual2.png",
        )

        self.client.post(f"/api/v1/outfits/try-on/{manual_1.id}/save/")
        self.client.post(f"/api/v1/outfits/try-on/{manual_2.id}/save/")

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 2)
        returned_job_ids = [item["outfit_job"]["id"] for item in saved_list]
        self.assertIn(manual_1.id, returned_job_ids)
        self.assertIn(manual_2.id, returned_job_ids)

    @patch("requests.get")
    def test_e_mixed_auto_and_manual_saved_outfits(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        date_1 = timezone.now().date() - timedelta(days=1)
        date_2 = timezone.now().date() - timedelta(days=3)

        auto_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_1,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto.png",
        )
        manual_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_2,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual.png",
        )

        self.client.post(f"/api/v1/outfits/try-on/{auto_job.id}/save/")
        self.client.post(f"/api/v1/outfits/try-on/{manual_job.id}/save/")

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 2)
        trigger_types = [item["outfit_job"]["trigger_type"] for item in saved_list]
        self.assertIn(TriggerType.AUTO, trigger_types)
        self.assertIn(TriggerType.MANUAL, trigger_types)

    @patch("requests.get")
    def test_f_user_isolation(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        date_now = timezone.now().date()

        user_a_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_now,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/usera.png",
        )
        user_b_job = OutfitJob.objects.create(
            user=self.other_user,
            avatar=self.avatar,
            scheduled_date=date_now - timedelta(days=1),
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/userb.png",
        )

        SavedOutfit.objects.create(user=self.user, outfit_job=user_a_job, date=date_now)
        SavedOutfit.objects.create(user=self.other_user, outfit_job=user_b_job, date=date_now - timedelta(days=1))

        # User A request
        get_resp_a = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp_a.status_code, status.HTTP_200_OK)
        saved_list_a = get_resp_a.data
        self.assertEqual(len(saved_list_a), 1)
        self.assertEqual(saved_list_a[0]["outfit_job"]["id"], user_a_job.id)

        # User B request
        self.client.force_authenticate(user=self.other_user)
        get_resp_b = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp_b.status_code, status.HTTP_200_OK)
        saved_list_b = get_resp_b.data
        self.assertEqual(len(saved_list_b), 1)
        self.assertEqual(saved_list_b[0]["outfit_job"]["id"], user_b_job.id)

    @patch("requests.get")
    def test_g_no_30_day_date_restriction(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        old_date = timezone.now().date() - timedelta(days=45)

        old_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=old_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/old.png",
        )
        SavedOutfit.objects.create(user=self.user, outfit_job=old_job, date=old_date)

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 1)
        self.assertEqual(saved_list[0]["outfit_job"]["id"], old_job.id)

    @patch("requests.get")
    def test_h_unsaved_outfit_job_excluded(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        date_now = timezone.now().date()

        unsaved_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_now,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/unsaved.png",
            is_saved=False,
        )

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 0)

    @patch("requests.get")
    def test_i_post_saved_endpoint_creates_record_and_get_returns_it(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=timezone.now().date(),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/post_save.png",
            is_saved=False,
        )

        post_resp = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-24",
            "note": "Great outfit",
        })
        self.assertEqual(post_resp.status_code, status.HTTP_201_CREATED)

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 1)
        self.assertEqual(saved_list[0]["outfit_job"]["id"], job.id)
        self.assertEqual(saved_list[0]["note"], "Great outfit")

    @patch("requests.get")
    def test_j_both_auto_and_manual_save_flows(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        date_1 = timezone.now().date() - timedelta(days=1)
        date_2 = timezone.now().date() - timedelta(days=2)

        auto_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_1,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_flow.png",
        )
        manual_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date_2,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_flow.png",
        )

        # Save auto job via try-on save endpoint
        save_auto_resp = self.client.post(f"/api/v1/outfits/try-on/{auto_job.id}/save/")
        self.assertEqual(save_auto_resp.status_code, status.HTTP_200_OK)

        # Save manual job via POST /saved/ endpoint
        save_manual_resp = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": manual_job.id,
            "saved_date": str(date_2),
        })
        self.assertEqual(save_manual_resp.status_code, status.HTTP_201_CREATED)

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 2)

    @patch("requests.get")
    def test_k_same_outfit_job_saved_twice_idempotent(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        today_date = timezone.now().date()
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/same_job.png",
        )

        # First save
        self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        # Second save attempt for the exact same job
        self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")

        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 1)
        self.assertEqual(saved_list[0]["outfit_job"]["id"], job.id)

    @patch("requests.get")
    def test_l_critical_regression_auto_and_manual_same_date_both_returned(self, mock_get, mock_cloud):
        self._mock_requests_get(mock_get)
        target_date = date.fromisoformat("2026-08-24")

        # OutfitJob #212 (e.g. manual try-on)
        job_212 = OutfitJob.objects.create(
            id=212,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=target_date,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/job212.png",
            is_saved=False,
        )
        # OutfitJob #214 (e.g. daily auto outfit)
        job_214 = OutfitJob.objects.create(
            id=214,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=target_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/job214.png",
            is_saved=False,
        )

        # Save #212
        save_212 = self.client.post(f"/api/v1/outfits/try-on/{job_212.id}/save/")
        self.assertEqual(save_212.status_code, status.HTTP_200_OK)

        # Save #214
        save_214 = self.client.post(f"/api/v1/outfits/try-on/{job_214.id}/save/")
        self.assertEqual(save_214.status_code, status.HTTP_200_OK)

        # Verify GET /saved/ returns BOTH #212 and #214
        get_resp = self.client.get("/api/v1/outfits/saved/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        saved_list = get_resp.data
        self.assertEqual(len(saved_list), 2)
        returned_job_ids = [item["outfit_job"]["id"] for item in saved_list]
        self.assertIn(212, returned_job_ids)
        self.assertIn(214, returned_job_ids)

        # Invariant check: OutfitJob.is_saved=True <-> SavedOutfit exists
        for jid in (212, 214):
            job_db = OutfitJob.objects.get(pk=jid)
            self.assertTrue(job_db.is_saved)
            self.assertTrue(SavedOutfit.objects.filter(user=self.user, outfit_job=job_db).exists())


class DailyOutfitSelectionAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="dailyuser@example.com",
            name="Daily User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="otherdaily@example.com",
            name="Other Daily User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )

        self.outfit_job_1 = OutfitJob.objects.create(
            id=221,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/outfit221.png",
            is_saved=False,
        )

        self.outfit_job_2 = OutfitJob.objects.create(
            id=219,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/outfit219.png",
            is_saved=False,
        )

        self.other_user_avatar = Avatar.objects.create(
            user=self.other_user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )
        self.other_user_job = OutfitJob.objects.create(
            id=300,
            user=self.other_user,
            avatar=self.other_user_avatar,
            scheduled_date=date(2026, 8, 25),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other300.png",
            is_saved=False,
        )

    def test_1_create_daily_selection(self):
        response = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 221,
        })
        self.assertIn(response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        self.assertTrue(DailyOutfitSelection.objects.filter(user=self.user, date="2026-08-25", outfit_job=self.outfit_job_1).exists())
        self.assertEqual(response.data["date"], "2026-08-25")
        self.assertEqual(response.data["outfit_id"], 221)
        self.assertEqual(response.data["status"], "done")

    def test_2_get_daily_selection(self):
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=date(2026, 8, 25),
            outfit_job=self.outfit_job_1,
        )
        response = self.client.get("/api/v1/outfits/daily-selection/?date=2026-08-25")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["date"], "2026-08-25")
        self.assertEqual(response.data["outfit_id"], 221)

    def test_3_replace_existing_selection(self):
        # Initial selection 221
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 221,
        })
        self.assertEqual(DailyOutfitSelection.objects.filter(user=self.user, date="2026-08-25").count(), 1)

        # Replace with selection 219
        response = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 219,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(DailyOutfitSelection.objects.filter(user=self.user, date="2026-08-25").count(), 1)

        selection = DailyOutfitSelection.objects.get(user=self.user, date="2026-08-25")
        self.assertEqual(selection.outfit_job_id, 219)

    def test_4_different_dates(self):
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 221,
        })
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-26",
            "outfit_id": 219,
        })
        self.assertEqual(DailyOutfitSelection.objects.filter(user=self.user).count(), 2)

    def test_5_different_users(self):
        # User A selects Outfit 221 for 2026-08-25
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 221,
        })

        # User B selects Outfit 300 for 2026-08-25
        self.client.force_authenticate(user=self.other_user)
        res_b = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 300,
        })
        self.assertIn(res_b.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        self.assertEqual(DailyOutfitSelection.objects.filter(date="2026-08-25").count(), 2)

    def test_6_cross_user_access(self):
        # User A attempts to select User B's outfit job 300
        response = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 300,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DailyOutfitSelection.objects.filter(user=self.user, date="2026-08-25").exists())

        # User B creates selection for 2026-08-25
        DailyOutfitSelection.objects.create(
            user=self.other_user,
            date=date(2026, 8, 25),
            outfit_job=self.other_user_job,
        )
        # User A GET request should not see User B's selection
        get_response = self.client.get("/api/v1/outfits/daily-selection/?date=2026-08-25")
        self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_7_missing_date(self):
        response = self.client.get("/api/v1/outfits/daily-selection/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_8_invalid_date(self):
        res1 = self.client.get("/api/v1/outfits/daily-selection/?date=invalid")
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)

        res2 = self.client.get("/api/v1/outfits/daily-selection/?date=2026-99-99")
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

        res3 = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "invalid-date",
            "outfit_id": 221,
        })
        self.assertEqual(res3.status_code, status.HTTP_400_BAD_REQUEST)

    def test_9_invalid_outfit(self):
        response = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 99999,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_10_failed_incomplete_outfit(self):
        pending_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.PENDING,
        )
        failed_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.FAILED,
        )

        res_pending = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": pending_job.id,
        })
        self.assertEqual(res_pending.status_code, status.HTTP_400_BAD_REQUEST)

        res_failed = self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": failed_job.id,
        })
        self.assertEqual(res_failed.status_code, status.HTTP_400_BAD_REQUEST)

    def test_11_saved_and_daily_selection_are_independent(self):
        # Set outfit 221 as saved
        self.outfit_job_1.is_saved = True
        self.outfit_job_1.save()
        SavedOutfit.objects.create(
            user=self.user,
            outfit_job=self.outfit_job_1,
            date=date(2026, 8, 25),
        )

        # Select 221 as daily selection
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 221,
        })
        self.outfit_job_1.refresh_from_db()
        self.assertTrue(self.outfit_job_1.is_saved)

        # Select 219 instead as daily selection
        self.client.post("/api/v1/outfits/daily-selection/", {
            "date": "2026-08-25",
            "outfit_id": 219,
        })
        self.outfit_job_1.refresh_from_db()
        self.outfit_job_2.refresh_from_db()

        self.assertTrue(self.outfit_job_1.is_saved)
        self.assertFalse(self.outfit_job_2.is_saved)
        self.assertEqual(DailyOutfitSelection.objects.get(user=self.user, date="2026-08-25").outfit_job_id, 219)

    def test_12_1_month_api_regression(self):
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=date(2026, 8, 25),
            outfit_job=self.outfit_job_1,
        )
        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in res.data]
        self.assertIn(221, ids)
        self.assertNotIn(219, ids)

    def test_13_delete_daily_selection(self):
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=date(2026, 8, 25),
            outfit_job=self.outfit_job_1,
        )
        response = self.client.delete("/api/v1/outfits/daily-selection/?date=2026-08-25")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(DailyOutfitSelection.objects.filter(user=self.user, date="2026-08-25").exists())
        # OutfitJob must NOT be deleted
        self.assertTrue(OutfitJob.objects.filter(pk=221).exists())


class OneMonthDailyOutfitSelectionAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="monthlyuser@example.com",
            name="Monthly User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="othermonthly@example.com",
            name="Other Monthly User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )
        self.other_avatar = Avatar.objects.create(
            user=self.other_user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )

    def test_1_only_daily_selected_outfits_are_returned(self):
        # Aug 25: Outfit 220, Outfit 221, Outfit 222 generated
        job_220 = OutfitJob.objects.create(id=220, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_221 = OutfitJob.objects.create(id=221, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_222 = OutfitJob.objects.create(id=222, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)

        # Select 221 as daily selection for 2026-08-25
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_221)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(returned_ids, [221])
        self.assertNotIn(220, returned_ids)
        self.assertNotIn(222, returned_ids)

    def test_2_multiple_days(self):
        job_218 = OutfitJob.objects.create(id=218, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 24), status=JobStatus.DONE)
        job_221 = OutfitJob.objects.create(id=221, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_230 = OutfitJob.objects.create(id=230, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 26), status=JobStatus.DONE)

        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 24), outfit_job=job_218)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_221)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 26), outfit_job=job_230)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(len(returned_ids), 3)
        self.assertIn(218, returned_ids)
        self.assertIn(221, returned_ids)
        self.assertIn(230, returned_ids)

    def test_3_day_without_selection(self):
        # Aug 26 generated outfits exist, but no DailyOutfitSelection
        OutfitJob.objects.create(id=230, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 26), status=JobStatus.DONE)
        OutfitJob.objects.create(id=231, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 26), status=JobStatus.DONE)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_4_saved_status_is_irrelevant(self):
        # Outfit 220 has is_saved = True
        job_220 = OutfitJob.objects.create(id=220, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE, is_saved=True)
        # Outfit 221 has is_saved = False
        job_221 = OutfitJob.objects.create(id=221, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE, is_saved=False)

        # Select 221 as daily selection
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_221)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(returned_ids, [221])
        self.assertNotIn(220, returned_ids)

    def test_5_user_isolation(self):
        job_a = OutfitJob.objects.create(id=500, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_b = OutfitJob.objects.create(id=501, user=self.other_user, avatar=self.other_avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)

        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_a)
        DailyOutfitSelection.objects.create(user=self.other_user, date=date(2026, 8, 25), outfit_job=job_b)

        # Authenticate User A
        self.client.force_authenticate(user=self.user)
        res_a = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual([item["id"] for item in res_a.data], [500])

        # Authenticate User B
        self.client.force_authenticate(user=self.other_user)
        res_b = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual([item["id"] for item in res_b.data], [501])

    def test_6_month_filtering(self):
        job_july = OutfitJob.objects.create(id=601, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 7, 31), status=JobStatus.DONE)
        job_aug1 = OutfitJob.objects.create(id=602, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 1), status=JobStatus.DONE)
        job_aug25 = OutfitJob.objects.create(id=603, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_sept = OutfitJob.objects.create(id=604, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 9, 1), status=JobStatus.DONE)

        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 7, 31), outfit_job=job_july)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 1), outfit_job=job_aug1)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_aug25)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 9, 1), outfit_job=job_sept)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(len(returned_ids), 2)
        self.assertIn(602, returned_ids)
        self.assertIn(603, returned_ids)
        self.assertNotIn(601, returned_ids)
        self.assertNotIn(604, returned_ids)

    def test_7_existing_date_parameter_behavior(self):
        job_aug1 = OutfitJob.objects.create(id=701, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 1), status=JobStatus.DONE)
        job_aug25 = OutfitJob.objects.create(id=702, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)

        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 1), outfit_job=job_aug1)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_aug25)

        # Query with start_date & end_date
        res_range = self.client.get("/api/v1/outfits/1-months/?start_date=2026-08-20&end_date=2026-08-30")
        self.assertEqual(res_range.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in res_range.data], [702])

        # Query with month date param (e.g. date=2026-08-01 -> month 2026-08)
        res_date = self.client.get("/api/v1/outfits/1-months/?date=2026-08-01")
        self.assertEqual(res_date.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in res_date.data], [701, 702])

    def test_exact_regression_user_scenario(self):
        # 1. Create OutfitJob 220 for test user (and unselected 221 on August 25)
        job_220 = OutfitJob.objects.create(
            id=220,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            status=JobStatus.DONE,
        )
        job_221 = OutfitJob.objects.create(
            id=221,
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 25),
            status=JobStatus.DONE,
        )

        # 2. Create DailyOutfitSelection: date=2026-08-25, outfit_job=220
        DailyOutfitSelection.objects.create(
            user=self.user,
            date=date(2026, 8, 25),
            outfit_job=job_220,
        )

        # 3. GET /api/v1/outfits/1-months/?month=8&year=2026
        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")

        # 4. Assert HTTP 200
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 5. Assert data is not empty
        self.assertTrue(len(res.data) > 0)

        # 6. Assert returned OutfitJob ID is 220
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(returned_ids, [220])

        # 7. Assert OutfitJob 221 is NOT returned
        self.assertNotIn(221, returned_ids)

    def test_one_selection_per_day(self):
        # Aug 25 -> Outfit A (older)
        job_a = OutfitJob.objects.create(id=801, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        sel_a = DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_a)

        # Aug 25 -> Outfit B (newer, updated/re-created selection)
        job_b = OutfitJob.objects.create(id=802, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        sel_a.outfit_job = job_b
        sel_a.save()

        # Aug 27 -> Outfit C
        job_c = OutfitJob.objects.create(id=803, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 27), status=JobStatus.DONE)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 27), outfit_job=job_c)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(len(returned_ids), 2)
        self.assertEqual(returned_ids, [802, 803])
        self.assertNotIn(801, returned_ids)

    def test_month_isolation_explicit(self):
        job_july = OutfitJob.objects.create(id=810, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 7, 31), status=JobStatus.DONE)
        job_aug25 = OutfitJob.objects.create(id=811, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_aug27 = OutfitJob.objects.create(id=812, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 27), status=JobStatus.DONE)
        job_sept = OutfitJob.objects.create(id=813, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 9, 1), status=JobStatus.DONE)

        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 7, 31), outfit_job=job_july)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_aug25)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 27), outfit_job=job_aug27)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 9, 1), outfit_job=job_sept)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(returned_ids, [811, 812])

    def test_latest_selection_wins(self):
        job_219 = OutfitJob.objects.create(id=821, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_220 = OutfitJob.objects.create(id=822, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_221 = OutfitJob.objects.create(id=823, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)

        sel = DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_219)
        sel.outfit_job = job_220
        sel.save()
        sel.outfit_job = job_221
        sel.save()

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        returned_ids = [item["id"] for item in res.data]
        self.assertEqual(returned_ids, [823])

    def test_empty_month(self):
        res = self.client.get("/api/v1/outfits/1-months/?month=12&year=2030")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_same_outfit_job_multiple_dates_regression(self):
        job_219 = OutfitJob.objects.create(id=919, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_220 = OutfitJob.objects.create(id=920, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 25), status=JobStatus.DONE)
        job_221 = OutfitJob.objects.create(id=921, user=self.user, avatar=self.avatar, scheduled_date=date(2026, 8, 26), status=JobStatus.DONE)

        # Aug 25 selection 1 (older)
        sel_25 = DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 25), outfit_job=job_219)
        # Aug 25 updated to Job 920 (newer)
        sel_25.outfit_job = job_220
        sel_25.save()

        # Aug 26 -> Job 921
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 26), outfit_job=job_221)
        # Aug 27 -> Job 921 (same OutfitJob as Aug 26)
        DailyOutfitSelection.objects.create(user=self.user, date=date(2026, 8, 27), outfit_job=job_221)

        res = self.client.get("/api/v1/outfits/1-months/?month=8&year=2026")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Assert exactly 3 records returned
        self.assertEqual(len(res.data), 3)

        items = [(item["id"], item["generated_date"]) for item in res.data]
        self.assertEqual(items, [(920, "2026-08-25"), (921, "2026-08-26"), (921, "2026-08-27")])


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class AvatarSelectionAndReasoningRegressionTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="regression_user@example.com",
            name="Regression User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="regression_other@example.com",
            name="Other User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.default_avatar = Avatar.objects.create(
            user=None,
            is_default=True,
            gender="male",
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_avatar.png",
        )

        self.cat_tops, _ = Category.objects.get_or_create(name="Tops")
        self.cat_bottoms, _ = Category.objects.get_or_create(name="Bottoms")

        self.item_top = self._create_completed_item(self.user, self.cat_tops, "Blue Shirt", "Blue")
        self.item_bottom = self._create_completed_item(self.user, self.cat_bottoms, "Black Pants", "Black")

    def _create_completed_item(self, user, category, description, color):
        img = SimpleUploadedFile("item.jpg", VALID_PNG_BYTES, content_type="image/png")
        item = WardrobeItem.objects.create(user=user, category=category, image=img)
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url=f"https://v3b.fal.media/item_{item.id}.png",
            description=description,
            color=color,
            is_saved=False,
        )
        return item

    # 1. User has only default avatar -> default avatar is selected
    def test_1_user_has_only_default_avatar(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        selected = resolve_auto_daily_avatar(self.user)
        self.assertEqual(selected.id, self.default_avatar.id)
        self.assertTrue(selected.is_default)

    # 2. User has default avatar + 1 completed generated avatar with valid Cloudinary image -> generated avatar is selected
    def test_2_user_has_default_and_one_completed_generated_avatar(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        gen_avatar = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/gen_1.png",
        )
        selected = resolve_auto_daily_avatar(self.user)
        self.assertEqual(selected.id, gen_avatar.id)
        self.assertFalse(selected.is_default)

    # 3. User has multiple completed generated avatars -> newest completed generated avatar is selected
    def test_3_user_has_multiple_completed_generated_avatars(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        gen_1 = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/gen_1.png",
        )
        gen_2 = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/gen_2.png",
        )
        selected = resolve_auto_daily_avatar(self.user)
        self.assertEqual(selected.id, gen_2.id)

    # 4. User has a newer generated avatar that is still processing/no valid Cloudinary image -> ignored, newest completed selected
    def test_4_newer_processing_or_unsaved_avatar_ignored(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        gen_completed = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/gen_completed.png",
        )
        # Newer processing avatar
        gen_processing = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.PROCESSING,
            is_saved=False,
        )
        # Newer unsaved avatar
        gen_unsaved = Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=False,
            fal_cdn_url="https://v3b.fal.media/cdn_only.png",
        )

        selected = resolve_auto_daily_avatar(self.user)
        self.assertEqual(selected.id, gen_completed.id)

    # 5. User has generated avatars but none has valid Cloudinary image -> default avatar selected
    def test_5_generated_avatars_without_cloudinary_image_fallback_to_default(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.PROCESSING,
            is_saved=False,
        )
        Avatar.objects.create(
            user=self.user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=False,
            fal_cdn_url="https://v3b.fal.media/cdn_only.png",
        )

        selected = resolve_auto_daily_avatar(self.user)
        self.assertEqual(selected.id, self.default_avatar.id)
        self.assertTrue(selected.is_default)

    # 6. Another user's generated avatar must never be selected
    def test_6_another_users_generated_avatar_never_selected(self, mock_cloud):
        from outfits.services import resolve_auto_daily_avatar
        other_gen = Avatar.objects.create(
            user=self.other_user,
            is_default=False,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/other_gen.png",
        )
        selected = resolve_auto_daily_avatar(self.user)
        self.assertNotEqual(selected.id, other_gen.id)
        self.assertEqual(selected.id, self.default_avatar.id)

    # 7. Successful AUTO outfit generation populates reasoning_title, reasoning_subtitle, reasoning_items, reasoning_note
    @patch("outfits.services.submit_try_on_job")
    def test_7_successful_auto_outfit_populates_reasoning_fields(self, mock_submit, mock_cloud):
        from outfits.services import get_or_create_today_auto_job
        job = get_or_create_today_auto_job(self.user)
        self.assertNotEqual(job.status, JobStatus.FAILED)
        self.assertTrue(bool(job.reasoning_title))
        self.assertTrue(bool(job.reasoning_subtitle))
        self.assertIsInstance(job.reasoning_items, list)
        self.assertGreater(len(job.reasoning_items), 0)
        self.assertTrue(
            all(
                isinstance(item, dict) and bool(item.get("title")) and bool(item.get("description"))
                for item in job.reasoning_items
            )
        )
        self.assertTrue(bool(job.reasoning_note))

    # 8. Successful MANUAL outfit generation populates reasoning_title, reasoning_subtitle, reasoning_items, reasoning_note
    @patch("outfits.services.submit_try_on_job")
    def test_8_successful_manual_outfit_populates_reasoning_fields(self, mock_submit, mock_cloud):
        response = self.client.post("/api/v1/outfits/try-on/", {
            "avatar_id": self.default_avatar.id,
            "wardrobe_item_ids": [self.item_top.id, self.item_bottom.id],
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        job_id = response.data["data"]["id"]
        job = OutfitJob.objects.get(pk=job_id)

        self.assertEqual(job.trigger_type, TriggerType.MANUAL)
        self.assertTrue(bool(job.reasoning_title))
        self.assertTrue(bool(job.reasoning_subtitle))
        self.assertIsInstance(job.reasoning_items, list)
        self.assertGreater(len(job.reasoning_items), 0)
        self.assertTrue(
            all(
                isinstance(item, dict) and bool(item.get("title")) and bool(item.get("description"))
                for item in job.reasoning_items
            )
        )
        self.assertTrue(bool(job.reasoning_note))

    # 9. Existing outfits with reasoning continue to serialize exactly as before
    def test_9_existing_outfits_serialization_contract(self, mock_cloud):
        from outfits.serializers import OutfitJobSerializer
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.default_avatar,
            scheduled_date=timezone.now().date(),
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            reasoning_title="Summer Vibe",
            reasoning_subtitle="Casual & Light",
            reasoning_items=[{"title": "Color Match", "description": "Blue and black pair well"}],
            reasoning_note="Great for warm weather",
        )
        serializer = OutfitJobSerializer(job)
        data = serializer.data

        self.assertEqual(data["reasoning_title"], "Summer Vibe")
        self.assertEqual(data["reasoning_subtitle"], "Casual & Light")
        self.assertEqual(data["reasoning_items"], [{"title": "Color Match", "description": "Blue and black pair well"}])
        self.assertEqual(data["reasoning_note"], "Great for warm weather")
        self.assertIn("saved", data)
        self.assertIn("is_saved", data)
        self.assertIn("generated_date", data)

    # 10. Existing saved/is_saved behavior remains unchanged
    def test_10_saved_behavior_unchanged(self, mock_cloud):
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.default_avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            is_saved=False,
        )
        self.assertFalse(job.is_saved)
        job.is_saved = True
        job.save()
        self.assertTrue(OutfitJob.objects.get(pk=job.id).is_saved)


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class AutoDailyWardrobeUsabilityRegressionTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="auto_usability_user@example.com",
            name="Auto Usability User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="auto_usability_other@example.com",
            name="Other User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.default_avatar = Avatar.objects.create(
            user=None,
            is_default=True,
            gender="male",
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_avatar.png",
        )

        self.cat_tops, _ = Category.objects.get_or_create(name="Tops")
        self.cat_bottoms, _ = Category.objects.get_or_create(name="Bottoms")
        self.cat_shoes, _ = Category.objects.get_or_create(name="Shoes")

    def _create_usable_item(self, user, category, description, color):
        img = SimpleUploadedFile("item.jpg", VALID_PNG_BYTES, content_type="image/png")
        item = WardrobeItem.objects.create(user=user, category=category, image=img)
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url=f"https://v3b.fal.media/item_{item.id}.png",
            description=description,
            color=color,
            is_saved=False,
        )
        return item

    # 1. Wardrobe items usable by manual try-on are eligible for AUTO daily generation
    @patch("outfits.services.submit_try_on_job")
    def test_usable_manual_item_eligible_for_auto(self, mock_submit, mock_cloud):
        top = self._create_usable_item(self.user, self.cat_tops, "Dress shirt white", "White")
        bottom = self._create_usable_item(self.user, self.cat_bottoms, "Dress pants black", "Black")

        # Verify manual try-on accepts them
        response = self.client.post("/api/v1/outfits/try-on/", {
            "avatar_id": self.default_avatar.id,
            "wardrobe_item_ids": [top.id, bottom.id],
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify AUTO generation selects them
        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)
        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertTrue(len(selected_ids) > 0)
        self.assertIn(top.id, selected_ids)
        self.assertIn(bottom.id, selected_ids)

    # 2. AUTO daily generation succeeds with ONE usable wardrobe item
    @patch("outfits.services.submit_try_on_job")
    def test_one_usable_wardrobe_item_succeeds(self, mock_submit, mock_cloud):
        top = self._create_usable_item(self.user, self.cat_tops, "Single White Tee", "White")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertEqual(selected_ids, [top.id])

    # 3. AUTO daily generation succeeds with TWO usable wardrobe items
    @patch("outfits.services.submit_try_on_job")
    def test_two_usable_wardrobe_items_succeeds(self, mock_submit, mock_cloud):
        top = self._create_usable_item(self.user, self.cat_tops, "Casual Shirt", "Blue")
        bottom = self._create_usable_item(self.user, self.cat_bottoms, "Chino Pants", "Beige")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertEqual(len(selected_ids), 2)
        self.assertIn(top.id, selected_ids)
        self.assertIn(bottom.id, selected_ids)

    # 4. User with shirt only (missing bottom) does not fail
    @patch("outfits.services.submit_try_on_job")
    def test_shirt_only_missing_bottom_succeeds(self, mock_submit, mock_cloud):
        shirt = self._create_usable_item(self.user, self.cat_tops, "Polo Shirt", "Green")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertEqual(selected_ids, [shirt.id])

    # 5. User with shoes only attempts generation and succeeds
    @patch("outfits.services.submit_try_on_job")
    def test_shoes_only_succeeds(self, mock_submit, mock_cloud):
        shoes = self._create_usable_item(self.user, self.cat_shoes, "Running Sneakers", "White")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertEqual(selected_ids, [shoes.id])

    # 6. User with incomplete categories (shirt + shoes, no bottom) succeeds
    @patch("outfits.services.submit_try_on_job")
    def test_incomplete_categories_shirt_and_shoes_succeeds(self, mock_submit, mock_cloud):
        shirt = self._create_usable_item(self.user, self.cat_tops, "Cotton Shirt", "White")
        shoes = self._create_usable_item(self.user, self.cat_shoes, "Loafers", "Brown")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        selected_ids = list(auto_job.wardrobe_items.values_list("id", flat=True))
        self.assertEqual(len(selected_ids), 2)
        self.assertIn(shirt.id, selected_ids)
        self.assertIn(shoes.id, selected_ids)

    # 7. AUTO daily generation does not incorrectly return error message when usable items exist
    @patch("outfits.services.submit_try_on_job")
    def test_auto_does_not_return_no_items_error_when_items_exist(self, mock_submit, mock_cloud):
        self._create_usable_item(self.user, self.cat_tops, "Classic Top", "Blue")
        self._create_usable_item(self.user, self.cat_bottoms, "Classic Pants", "Black")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)
        self.assertNotEqual(auto_job.error_message, "No completed wardrobe items available for daily outfit generation.")

    # 8. Exact scenario (items 108 and 109 equivalent) succeeds in AUTO generation
    @patch("outfits.services.submit_try_on_job")
    def test_auto_succeeds_for_items_108_and_109_equivalent(self, mock_submit, mock_cloud):
        item_108 = self._create_usable_item(self.user, self.cat_tops, "Formal Shirt", "Blue")
        item_109 = self._create_usable_item(self.user, self.cat_bottoms, "Tailored Trousers", "Grey")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertNotEqual(auto_job.status, JobStatus.FAILED)
        items = list(auto_job.wardrobe_items.all())
        self.assertEqual(len(items), 2)
        self.assertIn(item_108, items)
        self.assertIn(item_109, items)

    # 9. AUTO generation fails when there genuinely are no usable items
    def test_auto_fails_when_no_usable_items(self, mock_cloud):
        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)
        self.assertEqual(auto_job.status, JobStatus.FAILED)
        self.assertEqual(auto_job.error_message, "No completed wardrobe items available for daily outfit generation.")

    # 10. User isolation is enforced
    @patch("outfits.services.submit_try_on_job")
    def test_user_isolation_enforced(self, mock_submit, mock_cloud):
        self._create_usable_item(self.other_user, self.cat_tops, "Other Top", "Red")
        self._create_usable_item(self.other_user, self.cat_bottoms, "Other Pants", "Black")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)
        self.assertEqual(auto_job.status, JobStatus.FAILED)

    # 11. Reasoning validation for AUTO job
    @patch("outfits.services.submit_try_on_job")
    def test_auto_job_has_valid_non_empty_reasoning(self, mock_submit, mock_cloud):
        self._create_usable_item(self.user, self.cat_tops, "Blue Shirt", "Blue")
        self._create_usable_item(self.user, self.cat_bottoms, "Black Jeans", "Black")

        from outfits.services import get_or_create_today_auto_job
        auto_job = get_or_create_today_auto_job(self.user)

        self.assertTrue(bool(auto_job.reasoning_title))
        self.assertTrue(bool(auto_job.reasoning_subtitle))
        self.assertIsInstance(auto_job.reasoning_items, list)
        self.assertGreater(len(auto_job.reasoning_items), 0)
        self.assertTrue(
            all(
                isinstance(item, dict) and bool(item.get("title")) and bool(item.get("description"))
                for item in auto_job.reasoning_items
            )
        )
        self.assertTrue(bool(auto_job.reasoning_note))








