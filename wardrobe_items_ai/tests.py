from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from wardrobe.models import Category
from .models import ItemAnalysis, JobStatus, WardrobeItem
from .serializers import ItemAnalysisSerializer, ItemAnalysisTriggerSerializer
from .services import _friendly_error_message, _parse_json_response, sync_analysis_status

User = get_user_model()


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

    def test_serializer_excludes_internal_fields(self):
        fields = ItemAnalysisSerializer.Meta.fields
        self.assertNotIn("internal_error_detail", fields)
        self.assertNotIn("fal_request_id_bg_removal", fields)
        self.assertNotIn("fal_request_id_vision", fields)
        self.assertIn("color", fields)
        self.assertIn("description", fields)
        self.assertIn("error_message", fields)

    def test_trigger_serializer_validation(self):
        # Valid integer field
        valid = ItemAnalysisTriggerSerializer(data={"wardrobe_item_id": 42})
        self.assertTrue(valid.is_valid())
        self.assertEqual(valid.validated_data["wardrobe_item_id"], 42)

        # Valid alias field
        alias = ItemAnalysisTriggerSerializer(data={"wardrobe_item": 42})
        self.assertTrue(alias.is_valid())
        self.assertEqual(alias.validated_data["wardrobe_item_id"], 42)

        # Non-integer value
        invalid_type = ItemAnalysisTriggerSerializer(data={"wardrobe_item_id": "invalid_str"})
        self.assertFalse(invalid_type.is_valid())
        self.assertIn("wardrobe_item_id", invalid_type.errors)

        # Missing field
        missing = ItemAnalysisTriggerSerializer(data={})
        self.assertFalse(missing.is_valid())
        self.assertIn("wardrobe_item_id", missing.errors)

    def test_sync_analysis_status_is_read_only(self):
        user = User.objects.create_user(email="testuser_sync@example.com", name="Test Sync User", password="password123")
        category = Category.objects.create(name="Tops")
        item = WardrobeItem.objects.create(user=user, category=category, image="test.jpg")
        analysis = ItemAnalysis.objects.create(wardrobe_item=item, status=JobStatus.PENDING)

        with patch("wardrobe_items_ai.services.process_item") as mock_process:
            result = sync_analysis_status(analysis)
            mock_process.assert_not_called()
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

    def test_trigger_invalid_integer_type_returns_400(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": "not-an-integer"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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

    @patch("wardrobe_items_ai.views.submit_analysis_job")
    def test_trigger_success_returns_serialized_analysis(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")
        response = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["wardrobe_item"], self.item.pk)
        mock_submit.assert_called_once()

    @patch("wardrobe_items_ai.views.submit_analysis_job")
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

        # Second trigger should skip submit_analysis_job and return existing 200
        res2 = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["color"], "Blue")
        self.assertEqual(mock_submit.call_count, 1)

    @patch("wardrobe_items_ai.views.submit_analysis_job")
    def test_trigger_on_processing_item_skips_reprocessing(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:trigger")

        analysis = ItemAnalysis.objects.create(
            wardrobe_item=self.item,
            status=JobStatus.PROCESSING,
        )

        response = self.client.post(url, {"wardrobe_item_id": self.item.pk}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], JobStatus.PROCESSING)
        mock_submit.assert_not_called()

    def test_status_endpoint_returns_read_only_analysis(self):
        self.client.force_authenticate(user=self.user)
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=self.item,
            status=JobStatus.PENDING,
        )
        url = reverse("wardrobe_items_ai:status", kwargs={"pk": analysis.pk})
        with patch("wardrobe_items_ai.services.process_item") as mock_process:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["status"], JobStatus.PENDING)
            mock_process.assert_not_called()


import io
from PIL import Image

def _create_test_image():
    file_obj = io.BytesIO()
    img = Image.new("RGB", (10, 10), color="red")
    img.save(file_obj, "jpeg")
    file_obj.name = "test_item.jpg"
    file_obj.seek(0)
    return SimpleUploadedFile(file_obj.name, file_obj.read(), content_type="image/jpeg")


class WardrobeItemAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="itemuser@example.com", name="Item User", password="password123")
        self.other_user = User.objects.create_user(email="itemother@example.com", name="Other Item User", password="password123")
        self.cat_tops = Category.objects.create(name="Tops", slug="tops")
        self.cat_bottoms = Category.objects.create(name="Bottoms", slug="bottoms")
        self.client = APIClient()

    def test_create_wardrobe_item(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:item-list-create")
        image_file = _create_test_image()
        data = {
            "image": image_file,
            "category": self.cat_tops.pk,
            "season": "summer",
            "occasion": "casual",
            "purchase_source": "Store",
        }
        response = self.client.post(url, data, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.assertEqual(response.data["category"]["id"], self.cat_tops.pk)
        self.assertEqual(response.data["category"]["name"], "Tops")
        self.assertEqual(response.data["season"], "summer")

    def test_create_wardrobe_item_with_category_slug_or_name(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("wardrobe_items_ai:item-list-create")
        image_file = _create_test_image()
        data = {
            "image": image_file,
            "category": "tops",
            "season": "summer",
            "occasion": "casual",
        }
        response = self.client.post(url, data, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["category"]["id"], self.cat_tops.pk)



    def test_list_and_filter_wardrobe_items(self):
        self.client.force_authenticate(user=self.user)
        item1 = WardrobeItem.objects.create(user=self.user, category=self.cat_tops, season="summer", occasion="casual", image="a.jpg")
        item2 = WardrobeItem.objects.create(user=self.user, category=self.cat_bottoms, season="winter", occasion="formal", image="b.jpg")
        # Other user's item should never show up
        WardrobeItem.objects.create(user=self.other_user, category=self.cat_tops, season="summer", image="c.jpg")

        url = reverse("wardrobe_items_ai:item-list-create")

        # Unfiltered list
        res_all = self.client.get(url)
        self.assertEqual(res_all.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_all.data), 2)

        # Filter by category ID
        res_cat = self.client.get(f"{url}?category={self.cat_tops.pk}")
        self.assertEqual(len(res_cat.data), 1)
        self.assertEqual(res_cat.data[0]["id"], item1.pk)

        # Filter by category name
        res_cat_slug = self.client.get(f"{url}?category=tops")
        self.assertEqual(len(res_cat_slug.data), 1)
        self.assertEqual(res_cat_slug.data[0]["id"], item1.pk)

        # Filter by season
        res_season = self.client.get(f"{url}?season=winter")
        self.assertEqual(len(res_season.data), 1)
        self.assertEqual(res_season.data[0]["id"], item2.pk)

    def test_retrieve_and_delete_wardrobe_item(self):
        self.client.force_authenticate(user=self.user)
        item = WardrobeItem.objects.create(user=self.user, category=self.cat_tops, image="a.jpg")
        url = reverse("wardrobe_items_ai:item-detail", kwargs={"pk": item.pk})

        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_get.data["id"], item.pk)

        res_del = self.client.delete(url)
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(WardrobeItem.objects.filter(pk=item.pk).exists())

    @patch("wardrobe_items_ai.views.submit_analysis_job")
    def test_end_to_end_flow(self, mock_submit):
        self.client.force_authenticate(user=self.user)
        # 1. Single call: Create item (triggers AI processing inline)
        url_create = reverse("wardrobe_items_ai:item-list-create")
        image_file = _create_test_image()
        res_create = self.client.post(url_create, {
            "image": image_file,
            "category": self.cat_tops.pk,
            "season": "summer",
            "occasion": "casual",
        }, format="multipart")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED, res_create.data)
        self.assertIn("analysis", res_create.data)
        self.assertIsNotNone(res_create.data["analysis"])
        item_id = res_create.data["id"]
        analysis_id = res_create.data["analysis"]["id"]
        mock_submit.assert_called_once()

        # 2. Mark analysis as FAILED to test retry path via trigger
        analysis = ItemAnalysis.objects.get(pk=analysis_id)
        analysis.status = JobStatus.FAILED
        analysis.error_message = "Temporary failure"
        analysis.internal_error_detail = "API timeout"
        analysis.save()

        # 3. Trigger endpoint serves as retry path on failed item
        url_trigger = reverse("wardrobe_items_ai:trigger")
        res_trigger = self.client.post(url_trigger, {"wardrobe_item_id": item_id}, format="json")
        self.assertEqual(res_trigger.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_submit.call_count, 2)

        # 4. Retrieve status via status endpoint
        url_status = reverse("wardrobe_items_ai:status", kwargs={"pk": analysis_id})
        res_status = self.client.get(url_status)
        self.assertEqual(res_status.status_code, status.HTTP_200_OK)
        self.assertEqual(res_status.data["id"], analysis_id)




