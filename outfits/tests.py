import io
from unittest.mock import MagicMock, patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from avatars.models import Avatar
from outfits.models import JobStatus, OutfitJob, TriggerType
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
