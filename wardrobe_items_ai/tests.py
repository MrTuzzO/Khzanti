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

    # Test 9: Deleting a wardrobe item triggers automatic Cloudinary image cleanup
    @patch("cloudinary.uploader.destroy")
    def test_9_deleting_wardrobe_item_cleans_up_cloudinary_storage(self, mock_cloud_destroy, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test_delete.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            processed_image="wardrobe_items_ai/processed/test_delete_proc.png",
            is_saved=True,
        )

        response = self.client.delete(f"/api/v1/wardrobe-items-ai/items/{item.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(WardrobeItem.objects.filter(pk=item.id).exists())
        self.assertGreaterEqual(mock_cloud_destroy.call_count, 1)

    # Test 10: Status polling endpoint performs ZERO Cloudinary uploads
    def test_10_status_polling_performs_zero_cloudinary_uploads(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test_status.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.PROCESSING,
            fal_cdn_url="https://v3b.fal.media/files/b/status_test.png",
            is_saved=False,
        )

        mock_cloud_upload.reset_mock()
        res1 = self.client.get(f"/api/v1/wardrobe-items-ai/{item.id}/status/")
        res2 = self.client.get(f"/api/v1/wardrobe-items-ai/{item.id}/status/")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        mock_cloud_upload.assert_not_called()

    # Test 11: Background removal completed but vision still processing -> status remains processing, processed_image is null
    def test_11_bg_removal_complete_vision_processing_lifecycle(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test_lifecycle.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.PROCESSING,
            fal_cdn_url="https://v3b.fal.media/files/b/bg_only.png",
            is_saved=False,
            color="",
            description="",
        )

        res = self.client.get(f"/api/v1/wardrobe-items-ai/{item.id}/status/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "processing")
        self.assertEqual(res.data["fal_cdn_url"], "https://v3b.fal.media/files/b/bg_only.png")
        self.assertEqual(res.data["display_url"], "https://v3b.fal.media/files/b/bg_only.png")
        self.assertIsNone(res.data["processed_image"])
        self.assertFalse(res.data["is_saved"])
        self.assertIsNone(res.data["color"])
        self.assertIsNone(res.data["description"])

    # Test 12: Vision webhook completes -> status becomes DONE, color/description saved, processed_image remains null until explicit save
    def test_12_vision_webhook_completes_status_done_processed_image_null(self, mock_cloud_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image="wardrobe_items/originals/test_vision_done.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            fal_request_id_vision="req_vis_done_99",
            fal_cdn_url="https://v3b.fal.media/files/b/bg_result.png",
            status=ItemAnalysis.JobStatus.PROCESSING,
            is_saved=False,
        )

        payload = {
            "request_id": "req_vis_done_99",
            "status": "OK",
            "payload": {
                "output": '{"matches_category": true, "detected_item_type": "top", "color": "emerald green", "description": "Emerald green blouse"}'
            },
        }

        webhook_res = self.client.post("/api/v1/wardrobe-items-ai/webhook/vision/", data=payload, format="json")
        self.assertEqual(webhook_res.status_code, status.HTTP_200_OK)

        status_res = self.client.get(f"/api/v1/wardrobe-items-ai/{item.id}/status/")
        self.assertEqual(status_res.status_code, status.HTTP_200_OK)
        self.assertEqual(status_res.data["status"], "done")
        self.assertEqual(status_res.data["color"], "emerald green")
        self.assertEqual(status_res.data["description"], "Emerald green blouse")
        self.assertIsNone(status_res.data["processed_image"])
        self.assertFalse(status_res.data["is_saved"])
        self.assertEqual(status_res.data["display_url"], "https://v3b.fal.media/files/b/bg_result.png")


@patch("cloudinary.uploader.upload", return_value={"public_id": "test_public_id", "url": "https://res.cloudinary.com/test/image/upload/v1/test.png"})
class WardrobeNullNormalizationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reguser", name="Reg User", email="reguser@example.com", password="password123"
        )
        self.other_user = User.objects.create_user(
            username="otherreguser", name="Other Reg", email="otherreg@example.com", password="password123"
        )
        self.shirt_cat, _ = Category.objects.get_or_create(name="Shirt")
        self.client.force_authenticate(user=self.user)

    # 1. Missing nullable display image serializes as null rather than ""
    def test_missing_nullable_display_image_serializes_as_null(self, mock_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.shirt_cat,
            image="wardrobe_items/originals/test_item.png",
        )
        analysis = ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.PENDING,
            fal_cdn_url="",
            is_saved=False,
        )
        res = self.client.get(f"/api/v1/wardrobe-items-ai/{item.id}/status/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data["display_url"])
        self.assertIsNone(res.data["fal_cdn_url"])
        self.assertIsNone(res.data["processed_image"])

    # 2. Missing nullable result image serializes as null rather than ""
    def test_missing_nullable_result_image_serializes_as_null(self, mock_upload):
        from django.utils import timezone
        from avatars.models import Avatar
        from avatars.serializers import AvatarSerializer
        from outfits.models import JobStatus as OutfitJobStatus, OutfitJob, TriggerType
        from outfits.serializers import OutfitJobSerializer

        avatar = Avatar.objects.create(
            user=self.user,
            status=Avatar.JobStatus.PENDING,
            fal_cdn_url="",
            is_saved=False,
        )
        avatar_data = AvatarSerializer(avatar).data
        self.assertIsNone(avatar_data["result_image"])
        self.assertIsNone(avatar_data["fal_cdn_url"])

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=avatar,
            scheduled_date=timezone.now().date(),
            trigger_type=TriggerType.MANUAL,
            status=OutfitJobStatus.PENDING,
            fal_cdn_url="",
            is_saved=False,
        )
        job_data = OutfitJobSerializer(job).data
        self.assertIsNone(job_data["result_image"])

    # 3. Optional reasoning scalar fields serialize as null when unavailable
    def test_optional_reasoning_scalar_fields_serialize_as_null(self, mock_upload):
        from django.utils import timezone
        from avatars.models import Avatar
        from outfits.models import JobStatus as OutfitJobStatus, OutfitJob, TriggerType
        from outfits.serializers import OutfitJobSerializer

        avatar = Avatar.objects.create(user=self.user, status=Avatar.JobStatus.DONE)
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=avatar,
            scheduled_date=timezone.now().date(),
            trigger_type=TriggerType.AUTO,
            status=OutfitJobStatus.DONE,
            reasoning_title="",
            reasoning_subtitle="",
            reasoning_note="",
            error_message="",
        )
        data = OutfitJobSerializer(job).data
        self.assertIsNone(data["reasoning_title"])
        self.assertIsNone(data["reasoning_subtitle"])
        self.assertIsNone(data["reasoning_note"])
        self.assertIsNone(data["error_message"])

    # 4. Existing collection fields preserve their existing [] behavior
    def test_existing_collection_fields_preserve_empty_list_behavior(self, mock_upload):
        from django.utils import timezone
        from avatars.models import Avatar
        from outfits.models import JobStatus as OutfitJobStatus, OutfitJob, TriggerType
        from outfits.serializers import OutfitJobSerializer

        avatar = Avatar.objects.create(user=self.user, status=Avatar.JobStatus.DONE)
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=avatar,
            scheduled_date=timezone.now().date(),
            trigger_type=TriggerType.MANUAL,
            status=OutfitJobStatus.PENDING,
            reasoning_items=[],
        )
        data = OutfitJobSerializer(job).data
        self.assertEqual(data["wardrobe_items"], [])
        self.assertEqual(data["reasoning_items"], [])

    # 5. User isolation remains unchanged
    def test_user_isolation_remains_unchanged(self, mock_upload):
        other_item = WardrobeItem.objects.create(
            user=self.other_user,
            category=self.shirt_cat,
            image="wardrobe_items/originals/other_shirt.png",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=other_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other.png",
        )
        res = self.client.get(f"/api/v1/wardrobe-items-ai/items/{other_item.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 6. Existing wardrobe API response structure and types (booleans, numbers, non-empty strings) remain unchanged
    def test_existing_wardrobe_api_response_structure_preserved(self, mock_upload):
        item = WardrobeItem.objects.create(
            user=self.user,
            category=self.shirt_cat,
            image="wardrobe_items/originals/struct_test.png",
            season="summer",
            occasion="casual",
            purchase_source="Zara",
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/shirt.png",
            color="navy",
            description="Navy polo shirt",
            is_saved=False,
        )
        res = self.client.get(f"/api/v1/wardrobe-items-ai/items/{item.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(res.data.keys()),
            {"id", "image", "category", "season", "occasion", "purchase_source", "analysis", "created_at"}
        )
        self.assertEqual(
            set(res.data["analysis"].keys()),
            {"id", "wardrobe_item", "status", "is_saved", "display_url", "fal_cdn_url", "processed_image", "color", "description", "error_message", "created_at", "updated_at"}
        )
        # Verify numbers, booleans, non-empty strings preserved
        self.assertIsInstance(res.data["id"], int)
        self.assertIsInstance(res.data["analysis"]["id"], int)
        self.assertIs(res.data["analysis"]["is_saved"], False)
        self.assertEqual(res.data["season"], "summer")
        self.assertEqual(res.data["occasion"], "casual")
        self.assertEqual(res.data["purchase_source"], "Zara")
        self.assertEqual(res.data["analysis"]["color"], "navy")
        self.assertEqual(res.data["analysis"]["description"], "Navy polo shirt")
        self.assertIsNone(res.data["analysis"]["error_message"])





