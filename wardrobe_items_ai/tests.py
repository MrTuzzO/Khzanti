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
from .serializers import ItemAnalysisSerializer, ItemAnalysisTriggerSerializer
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

    def test_trigger_serializer_validation(self):
        valid = ItemAnalysisTriggerSerializer(data={"wardrobe_item_id": 42})
        self.assertTrue(valid.is_valid())
        self.assertEqual(valid.validated_data["wardrobe_item_id"], 42)

        alias = ItemAnalysisTriggerSerializer(data={"wardrobe_item": 42})
        self.assertTrue(alias.is_valid())
        self.assertEqual(alias.validated_data["wardrobe_item_id"], 42)

        invalid_type = ItemAnalysisTriggerSerializer(data={"wardrobe_item_id": "invalid_str"})
        self.assertFalse(invalid_type.is_valid())
        self.assertIn("wardrobe_item_id", invalid_type.errors)

        missing = ItemAnalysisTriggerSerializer(data={})
        self.assertFalse(missing.is_valid())
        self.assertIn("wardrobe_item_id", missing.errors)

    def test_sync_analysis_status_is_read_only(self):
        user = User.objects.create_user(email="testuser_sync@example.com", name="Test Sync User", password="password123")
        category = Category.objects.create(name="Tops")
        item = WardrobeItem.objects.create(user=user, category=category, image="test.jpg")
        analysis = ItemAnalysis.objects.create(wardrobe_item=item, status=JobStatus.PENDING)

        result = sync_analysis_status(analysis)
        self.assertEqual(result.status, JobStatus.PENDING)


class ItemAnalysisAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="testuser@example.com", name="Test User", password="password123")
        self.other_user = User.objects.create_user(email="otheruser@example.com", name="Other User", password="password123")

        self.category = Category.objects.create(name="Shirts")
        self.item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="items/test.jpg",
        )
        self.other_item = WardrobeItem.objects.create(
            user=self.other_user,
            category=self.category,
            image="items/other.jpg",
        )
        self.client = APIClient()

    def test_trigger_unauthenticated_returns_401(self):
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_trigger_missing_payload_returns_400(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("wardrobe_item_id", response.data)

    def test_trigger_non_existent_item_returns_404(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": 999999}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_trigger_other_user_item_returns_404(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": self.other_item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async", new_callable=AsyncMock)
    def test_trigger_success_returns_serialized_analysis(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["wardrobe_item"], self.item.pk)
        mock_submit.assert_called_once()

    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async", new_callable=AsyncMock)
    def test_trigger_twice_on_same_item_executes_job_only_once(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")

        # First trigger creates and submits job
        res1 = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mock_submit.call_count, 1)

        # Mark item as DONE
        analysis = ItemAnalysis.objects.get(wardrobe_item=self.item)
        analysis.status = JobStatus.DONE
        analysis.color = "Blue"
        analysis.save()

        # Second trigger should skip submit_bg_removal_job_async and return existing 200
        res2 = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["color"], "Blue")
        self.assertEqual(mock_submit.call_count, 1)

    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async", new_callable=AsyncMock)
    def test_trigger_on_processing_item_skips_reprocessing(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")

        ItemAnalysis.objects.create(
            wardrobe_item=self.item,
            status=JobStatus.PROCESSING,
        )

        response = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], JobStatus.PROCESSING)
        mock_submit.assert_not_called()


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
