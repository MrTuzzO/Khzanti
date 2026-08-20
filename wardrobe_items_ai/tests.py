from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from wardrobe.models import Category
from wardrobe_items_ai.models import ItemAnalysis, WardrobeItem

User = get_user_model()

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc` \x05\x00\x00"
    b"\x04\x00\x01\x04\x05d\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def create_test_image_file():
    buf = BytesIO()
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile("test_item.png", buf.read(), content_type="image/png")


@patch("cloudinary.uploader.upload", return_value={"public_id": "test_public_id", "url": "https://res.cloudinary.com/test/image/upload/v1/test.png"})
class WardrobeItemOnDemandCloudinaryTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="itemuser", name="Item User", email="itemuser@example.com", password="password123"
        )
        self.other_user = User.objects.create_user(
            username="otheruser", name="Other User", email="otheruser@example.com", password="password123"
        )
        self.category = Category.objects.create(name="Tops")
        self.client.force_authenticate(user=self.user)

    # Test 1: User uploads wardrobe item -> WardrobeItem.image uploaded to Cloudinary
    @patch("fal_client.submit")
    def test_1_user_upload_creates_original_cloudinary_image(self, mock_fal_submit, mock_cloud_upload):
        mock_handle = MagicMock()
        mock_handle.request_id = "req_bg_123"
        mock_fal_submit.return_value = mock_handle

        test_file = create_test_image_file()
        response = self.client.post(
            "/api/v1/wardrobe-items-ai/items/",
            {"image": test_file, "category": self.category.id},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = WardrobeItem.objects.get(pk=response.data["id"])
        self.assertTrue(bool(item.image))

    # Test 2: Background removal & vision complete -> fal_cdn_url set, processed_image empty, is_saved=False, NO Cloudinary upload
    def test_2_webhook_vision_completion_does_not_upload_to_cloudinary(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            fal_request_id_vision="req_vis_123",
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            status=ItemAnalysis.JobStatus.PROCESSING,
            is_saved=False,
        )

        mock_cloud_upload.reset_mock()

        payload = {
            "request_id": "req_vis_123",
            "status": "OK",
            "payload": {
                "output": '{"matches_category": true, "detected_item_type": "top", "color": "blue", "description": "Blue top"}'
            },
        }

        response = self.client.post(
            "/api/v1/wardrobe-items-ai/webhook/vision/",
            data=payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, ItemAnalysis.JobStatus.DONE)
        self.assertEqual(analysis.fal_cdn_url, "https://v3b.fal.media/files/b/processed.png")
        self.assertFalse(analysis.is_saved)
        self.assertFalse(bool(analysis.processed_image))
        self.assertEqual(analysis.display_url, "https://v3b.fal.media/files/b/processed.png")

        # Verify NO Cloudinary upload calls were executed during vision webhook
        mock_cloud_upload.assert_not_called()

    # Test 3: Try-on after processing but before save uses fal_cdn_url
    def test_3_tryon_uses_fal_cdn_url_before_save(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            is_saved=False,
        )
        self.assertEqual(analysis.display_url, "https://v3b.fal.media/files/b/processed.png")

    # Test 4: User calls POST /items/{id}/save/ -> is_saved=True, processed_image -> Cloudinary
    def test_4_explicit_save_endpoint_uploads_to_cloudinary(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            is_saved=False,
        )

        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            response = self.client.post(f"/api/v1/wardrobe-items-ai/items/{item.id}/save/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        analysis.refresh_from_db()
        self.assertTrue(analysis.is_saved)
        self.assertTrue(bool(analysis.processed_image))
        self.assertIn("is_saved", response.data["analysis"])
        self.assertTrue(response.data["analysis"]["is_saved"])

    # Test 5: Call save endpoint twice -> Second call is idempotent and does not create duplicate upload
    def test_5_save_endpoint_is_idempotent(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            is_saved=False,
        )

        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            res1 = self.client.post(f"/api/v1/wardrobe-items-ai/items/{item.id}/save/")
            self.assertEqual(res1.status_code, status.HTTP_200_OK)
            get_count_after_first_save = mock_get.call_count

            # Second save call
            res2 = self.client.post(f"/api/v1/wardrobe-items-ai/items/{item.id}/save/")
            self.assertEqual(res2.status_code, status.HTTP_200_OK)
            self.assertEqual(mock_get.call_count, get_count_after_first_save)

    # Test 6: Unauthorized user cannot save another user's wardrobe item
    def test_6_unauthorized_user_cannot_save_other_users_item(self, mock_cloud_upload):
        other_item = WardrobeItem.objects.create(
            user=self.other_user,
            category=self.category,
            image="wardrobe_items/originals/other.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=other_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/files/b/other.png",
            is_saved=False,
        )

        response = self.client.post(f"/api/v1/wardrobe-items-ai/items/{other_item.id}/save/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # Test 7: Webhook called multiple times -> No duplicate Cloudinary uploads
    def test_7_webhook_idempotency_does_not_upload_to_cloudinary(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            fal_request_id_vision="req_vis_dup",
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            status=ItemAnalysis.JobStatus.PROCESSING,
            is_saved=False,
        )

        mock_cloud_upload.reset_mock()
        payload = {
            "request_id": "req_vis_dup",
            "status": "OK",
            "payload": {
                "output": '{"matches_category": true, "detected_item_type": "top", "color": "blue", "description": "Blue top"}'
            },
        }

        res1 = self.client.post("/api/v1/wardrobe-items-ai/webhook/vision/", data=payload, format="json")
        res2 = self.client.post("/api/v1/wardrobe-items-ai/webhook/vision/", data=payload, format="json")

        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        mock_cloud_upload.assert_not_called()

    # Test 8: Existing wardrobe item functionality continues working
    def test_8_existing_wardrobe_item_list_and_detail_views(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/files/b/processed.png",
            is_saved=False,
        )

        res_list = self.client.get("/api/v1/wardrobe-items-ai/items/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        items_list = res_list.data["results"] if isinstance(res_list.data, dict) and "results" in res_list.data else res_list.data
        self.assertEqual(len(items_list), 1)

        res_detail = self.client.get(f"/api/v1/wardrobe-items-ai/items/{item.id}/")
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_detail.data["analysis"]["fal_cdn_url"], "https://v3b.fal.media/files/b/processed.png")
