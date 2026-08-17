import io
from unittest.mock import MagicMock, patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from wardrobe.models import Category
from wardrobe_items_ai.models import ItemAnalysis, WardrobeItem

User = get_user_model()


def generate_test_image_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="green")
    img.save(buf, format="PNG")
    return buf.getvalue()


VALID_PNG_BYTES = generate_test_image_bytes()

MOCK_CLOUDINARY_RESPONSE = {
    "public_id": "wardrobe_items_ai/processed/test_item",
    "version": 1234567890,
    "width": 100,
    "height": 100,
    "format": "png",
    "resource_type": "image",
    "created_at": "2026-08-17T00:00:00Z",
    "bytes": 68,
    "type": "upload",
    "url": "http://res.cloudinary.com/test/image/upload/v1234567890/test_item.png",
    "secure_url": "https://res.cloudinary.com/test/image/upload/v1234567890/test_item.png",
}


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class SaveWardrobeItemAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="wardrobeuser@example.com",
            name="Wardrobe User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.other_user = User.objects.create_user(
            email="otherwardrobeuser@example.com",
            name="Other Wardrobe User",
            password="password123",
        )

        self.category, _ = Category.objects.get_or_create(name="Shoes", defaults={"slug": "shoes"})
        self.dummy_image = SimpleUploadedFile(
            name="shoes.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )

        self.item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image=self.dummy_image,
        )

        self.analysis = ItemAnalysis.objects.create(
            wardrobe_item=self.item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/shoes_bg_removed.png",
            color="black",
            description="Black leather running shoes",
            is_saved=False,
        )

    @patch("requests.get")
    def test_save_wardrobe_item_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        # 1. Initially display_url points to fal.ai CDN URL and is_saved=False
        detail_resp = self.client.get(f"/api/v1/wardrobe-items-ai/items/{self.item.id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        analysis_data = detail_resp.data["analysis"]
        self.assertEqual(analysis_data["display_url"], "https://v3b.fal.media/shoes_bg_removed.png")
        self.assertFalse(analysis_data["saved"])

        # 2. Save wardrobe item (downloads fal URL and uploads to Cloudinary)
        save_resp = self.client.post(f"/api/v1/wardrobe-items-ai/items/{self.item.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        saved_analysis_data = save_resp.data["analysis"]
        self.assertTrue(saved_analysis_data["saved"])
        self.assertIn("http", saved_analysis_data["display_url"])

        mock_requests_get.assert_called_once()
        self.analysis.refresh_from_db()
        self.assertTrue(self.analysis.is_saved)

        # 3. Original uploaded image remains unchanged
        self.item.refresh_from_db()
        self.assertTrue(bool(self.item.image))

        # 4. Duplicate save call is idempotent
        save_resp_2 = self.client.post(f"/api/v1/wardrobe-items-ai/items/{self.item.id}/save/")
        self.assertEqual(save_resp_2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_requests_get.call_count, 1)

    def test_user_cannot_save_other_user_wardrobe_item(self, mock_cloud):
        other_item = WardrobeItem.objects.create(
            user=self.other_user,
            category=self.category,
            image=self.dummy_image,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=other_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other_shoes.png",
            is_saved=False,
        )

        response = self.client.post(f"/api/v1/wardrobe-items-ai/items/{other_item.id}/save/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
