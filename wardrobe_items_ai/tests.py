import io
import json
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from wardrobe.models import Category
from .models import ItemAnalysis, JobStatus, WardrobeItem
from .serializers import ItemAnalysisSerializer
from .services import _friendly_error_message, _parse_json_response, sync_analysis_status, verify_webhook_signature

User = get_user_model()


def _create_test_image():
    file_obj = io.BytesIO()
    img = Image.new("RGB", (10, 10), color="red")
    img.save(file_obj, "jpeg")
    file_obj.name = "test_item.jpg"
    file_obj.seek(0)
    return SimpleUploadedFile(file_obj.name, file_obj.read(), content_type="image/jpeg")


class ItemAnalysisUnitTests(TestCase):
    def test_friendly_error_message_mapping(self):
        self.assertIn("unavailable", _friendly_error_message("Billing credit exhausted"))
        self.assertIn("safety policies", _friendly_error_message("NSFW content detected"))
        self.assertIn("timed out", _friendly_error_message("Request timeout after 30s"))
        self.assertIn("Failed to analyze", _friendly_error_message("Random unhandled crash"))

    def test_parse_json_response(self):
        json_str = '{"matches_category": true, "color": "Navy Blue", "description": "Classic blazer"}'
        parsed = _parse_json_response(json_str)
        self.assertTrue(parsed.get("matches_category"))
        self.assertEqual(parsed.get("color"), "Navy Blue")
        self.assertEqual(parsed.get("description"), "Classic blazer")

        embedded_str = 'Here is the result:\n```json\n{"matches_category": false, "color": "Red", "description": "High heels"}\n```'
        parsed_embedded = _parse_json_response(embedded_str)
        self.assertFalse(parsed_embedded.get("matches_category"))
        self.assertEqual(parsed_embedded.get("color"), "Red")

    def test_serializer_includes_fal_cdn_and_display_url(self):
        fields = ItemAnalysisSerializer.Meta.fields
        self.assertNotIn("internal_error_detail", fields)
        self.assertIn("color", fields)
        self.assertIn("description", fields)
        self.assertIn("error_message", fields)
        self.assertIn("fal_cdn_url", fields)
        self.assertIn("display_url", fields)

    def test_display_url_fallback_logic(self):
        user = User.objects.create_user(email="display_url_test@example.com", name="Display Test", password="password123")
        category = Category.objects.create(name="Tops")
        item = WardrobeItem.objects.create(user=user, category=category, image="test.jpg")
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=JobStatus.PROCESSING,
            fal_cdn_url="https://fal.media/files/test.png",
        )

        # Before Cloudinary swap, display_url returns fal_cdn_url
        self.assertEqual(analysis.display_url, "https://fal.media/files/test.png")


class ItemAnalysisAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="testuser@example.com", name="Test User", password="password123")
        self.other_user = User.objects.create_user(email="otheruser@example.com", name="Other User", password="password123")

        self.category_shoes, _ = Category.objects.get_or_create(name="Shoes", defaults={"slug": "shoes"})
        self.category_borkha, _ = Category.objects.get_or_create(name="Borkha", defaults={"slug": "borkha"})

        self.item_shoes = WardrobeItem.objects.create(
            user=self.user,
            category=self.category_shoes,
            image="items/shoes.jpg",
        )
        self.analysis_shoes = ItemAnalysis.objects.create(
            wardrobe_item=self.item_shoes,
            status=JobStatus.FAILED,
            internal_error_detail="Category mismatch: vision model reported image does not match category 'Shoes'.",
            error_message="We couldn't find a clear Shoes in this photo.",
        )

        self.item_borkha = WardrobeItem.objects.create(
            user=self.user,
            category=self.category_borkha,
            image="items/borkha.jpg",
        )
        self.analysis_borkha = ItemAnalysis.objects.create(
            wardrobe_item=self.item_borkha,
            status=JobStatus.PROCESSING,
        )

        self.client = APIClient()

    def test_status_endpoint_lookup_by_wardrobe_item_id(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:status", kwargs={"pk": self.item_borkha.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["wardrobe_item"], self.item_borkha.pk)
        self.assertEqual(response.data["status"], "processing")

    @patch("wardrobe_items_ai.services.fal_client.status")
    def test_status_endpoint_does_not_trigger_fal_network_polling(self, mock_fal_status):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:status", kwargs={"pk": self.item_borkha.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_fal_status.assert_not_called()

    def test_delete_item_returns_200_ok_with_confirmation(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:item-detail", kwargs={"pk": self.item_shoes.pk})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        self.assertFalse(WardrobeItem.objects.filter(pk=self.item_shoes.pk).exists())





class WebhookViewAPITests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(email="webhookuser@example.com", name="Webhook User", password="password123")
        self.category = Category.objects.create(name="Tops")
        self.item = WardrobeItem.objects.create(user=self.user, category=self.category, image="test.jpg")
        self.analysis = ItemAnalysis.objects.create(
            wardrobe_item=self.item,
            status=JobStatus.PROCESSING,
            fal_request_id_bg_removal="req_bg_123",
            fal_request_id_vision="req_vis_456",
        )
        self.client = APIClient()

    def test_bg_removal_webhook_missing_request_id_returns_400(self):
        url = reverse("wardrobe_items_ai:webhook-bg-removal")
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("wardrobe_items_ai.views.submit_vision_job_async", new_callable=AsyncMock)
    def test_bg_removal_webhook_success_updates_fal_cdn_url(self, mock_vision_submit):
        url = reverse("wardrobe_items_ai:webhook-bg-removal")
        payload = {
            "request_id": "req_bg_123",
            "status": "OK",
            "payload": {
                "image": {"url": "https://fal.media/files/processed.png"}
            }
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.fal_cdn_url, "https://fal.media/files/processed.png")
        mock_vision_submit.assert_called_once()

    @patch("requests.get")
    def test_vision_webhook_category_mismatch_marks_failed(self, mock_get):
        url = reverse("wardrobe_items_ai:webhook-vision")
        self.analysis.fal_cdn_url = "https://fal.media/files/processed.png"
        self.analysis.save()

        payload = {
            "request_id": "req_vis_456",
            "status": "OK",
            "payload": {
                "output": '{"matches_category": false, "color": "Blue", "description": "Shoes"}'
            }
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, JobStatus.FAILED)
        self.assertIn("category mismatch", self.analysis.internal_error_detail.lower())
