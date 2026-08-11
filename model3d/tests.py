from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from avatars.models import Avatar
from outfits.models import JobStatus as OutfitJobStatus, OutfitJob
from model3d.models import ConversionStatus, ThreeDConversion

User = get_user_model()


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class Model3DAPITests(APITestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            email="usera@example.com", password="password123", name="User A"
        )
        self.user_b = User.objects.create_user(
            email="userb@example.com", password="password123", name="User B"
        )

        self.avatar_a = Avatar.objects.create(
            user=self.user_a,
            status=Avatar.JobStatus.DONE,
        )
        self.avatar_b = Avatar.objects.create(
            user=self.user_b,
            status=Avatar.JobStatus.DONE,
        )

        # Completed OutfitJob with result_image path for User A
        self.completed_job_a = OutfitJob.objects.create(
            user=self.user_a,
            avatar=self.avatar_a,
            status=OutfitJobStatus.DONE,
            result_image="outfits/result/sample_outfit.jpg",
        )

        # Pending OutfitJob for User A
        self.pending_job_a = OutfitJob.objects.create(
            user=self.user_a,
            avatar=self.avatar_a,
            status=OutfitJobStatus.PENDING,
        )

        # Processing OutfitJob for User A
        self.processing_job_a = OutfitJob.objects.create(
            user=self.user_a,
            avatar=self.avatar_a,
            status=OutfitJobStatus.PROCESSING,
        )

        # Failed OutfitJob for User A
        self.failed_job_a = OutfitJob.objects.create(
            user=self.user_a,
            avatar=self.avatar_a,
            status=OutfitJobStatus.FAILED,
        )

        # Completed OutfitJob without result_image for User A
        self.no_image_job_a = OutfitJob.objects.create(
            user=self.user_a,
            avatar=self.avatar_a,
            status=OutfitJobStatus.DONE,
            result_image="",
        )

        self.convert_url = reverse("model3d:convert")
        self.webhook_url = reverse("model3d:webhook")

    # --- Authentication Tests ---

    def test_unauthenticated_user_cannot_create_conversion(self):
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_user_cannot_retrieve_conversion(self):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="test_req_123",
        )
        detail_url = reverse("model3d:conversion-detail", kwargs={"pk": conversion.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Ownership Security Tests ---

    @patch("fal_client.submit")
    def test_user_a_can_convert_own_outfit(self, mock_fal_submit):
        mock_handle = MagicMock()
        mock_handle.request_id = "fal_req_001"
        mock_fal_submit.return_value = mock_handle

        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["data"]["status"], "processing")
        self.assertEqual(response.data["data"]["fal_request_id"], "fal_req_001")
        self.assertIn("result_mesh_url", response.data["data"])

    def test_user_b_cannot_convert_user_a_outfit(self):
        self.client.force_authenticate(user=self.user_b)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_b_cannot_retrieve_user_a_conversion(self):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="test_req_123",
        )
        detail_url = reverse("model3d:conversion-detail", kwargs={"pk": conversion.id})

        self.client.force_authenticate(user=self.user_b)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- Outfit Validation Tests ---

    def test_cannot_convert_pending_outfit_job(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.pending_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not ready", str(response.data.get("message", "")).lower())

    def test_cannot_convert_processing_outfit_job(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.processing_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not ready", str(response.data.get("message", "")).lower())

    def test_cannot_convert_failed_outfit_job(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.failed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("failed", str(response.data.get("message", "")).lower())

    def test_cannot_convert_outfit_job_without_result_image(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.no_image_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Hunyuan 3D v3.1 Pro Submission Parameter Tests ---

    @patch("fal_client.submit")
    def test_fal_submission_uses_hunyuan_3d_pro_model_and_input_image_url(self, mock_fal_submit):
        mock_handle = MagicMock()
        mock_handle.request_id = "fal_req_hunyuan"
        mock_fal_submit.return_value = mock_handle

        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_fal_submit.assert_called_once()
        args, kwargs = mock_fal_submit.call_args
        self.assertEqual(args[0], "fal-ai/hunyuan-3d/v3.1/pro/image-to-3d")
        self.assertIn("input_image_url", kwargs["arguments"])
        self.assertEqual(kwargs["arguments"]["input_image_url"], self.completed_job_a.result_image.url)
        self.assertNotIn("image_url", kwargs["arguments"])

    # --- Duplicate Conversion Protection & Retry Tests ---

    @patch("fal_client.submit")
    def test_duplicate_request_while_processing_returns_same_conversion(
        self, mock_fal_submit
    ):
        mock_handle = MagicMock()
        mock_handle.request_id = "fal_req_dup_1"
        mock_fal_submit.return_value = mock_handle

        self.client.force_authenticate(user=self.user_a)

        # First request
        resp1 = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        conv1_id = resp1.data["data"]["id"]
        self.assertEqual(mock_fal_submit.call_count, 1)

        # Second request (duplicate while processing)
        resp2 = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data["data"]["id"], conv1_id)
        self.assertEqual(mock_fal_submit.call_count, 1)

    @patch("fal_client.submit")
    def test_duplicate_request_after_done_returns_same_conversion(self, mock_fal_submit):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.DONE,
            fal_request_id="fal_req_completed",
            result_mesh_url="https://v3b.fal.media/files/sample/model.glb",
        )

        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], conversion.id)
        self.assertEqual(response.data["data"]["status"], "done")
        self.assertEqual(
            response.data["data"]["result_mesh_url"],
            "https://v3b.fal.media/files/sample/model.glb",
        )
        mock_fal_submit.assert_not_called()

    @patch("fal_client.submit")
    def test_failed_conversion_can_be_retried(self, mock_fal_submit):
        ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.FAILED,
            error_message="Previous error",
        )

        mock_handle = MagicMock()
        mock_handle.request_id = "fal_req_retry"
        mock_fal_submit.return_value = mock_handle

        self.client.force_authenticate(user=self.user_a)
        response = self.client.post(
            self.convert_url, {"outfit_job_id": self.completed_job_a.id}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["status"], "processing")
        self.assertEqual(response.data["data"]["fal_request_id"], "fal_req_retry")
        self.assertEqual(mock_fal_submit.call_count, 1)

    # --- Hunyuan 3D Webhook & Output Parsing Tests ---

    @patch("requests.get")
    def test_successful_hunyuan_webhook_model_glb_structure(self, mock_requests_get):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="fal_req_hunyuan_success_1",
        )

        fal_glb_url = "https://v3b.fal.media/files/b/0aa5dca1/Ute-7509uzk3GfGyJZGTL_combined_bodies.glb"
        payload = {
            "request_id": "fal_req_hunyuan_success_1",
            "status": "OK",
            "payload": {
                "model_glb": {
                    "url": fal_glb_url,
                    "content_type": "model/gltf-binary",
                }
            },
        }

        response = self.client.post(self.webhook_url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        conversion.refresh_from_db()
        self.assertEqual(conversion.status, ConversionStatus.DONE)
        self.assertEqual(conversion.result_mesh_url, fal_glb_url)

        # Verify zero HTTP GET requests were made to download the GLB
        mock_requests_get.assert_not_called()

    @patch("requests.get")
    def test_successful_hunyuan_webhook_model_urls_glb_structure(self, mock_requests_get):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="fal_req_hunyuan_success_2",
        )

        fal_glb_url = "https://v3b.fal.media/files/b/0aa5dca1/model.glb"
        payload = {
            "request_id": "fal_req_hunyuan_success_2",
            "status": "OK",
            "payload": {
                "model_urls": {
                    "glb": {
                        "url": fal_glb_url,
                    }
                }
            },
        }

        response = self.client.post(self.webhook_url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        conversion.refresh_from_db()
        self.assertEqual(conversion.status, ConversionStatus.DONE)
        self.assertEqual(conversion.result_mesh_url, fal_glb_url)
        mock_requests_get.assert_not_called()

    @patch("requests.get")
    def test_webhook_missing_glb_url_marks_conversion_failed(self, mock_requests_get):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="fal_req_no_glb",
        )

        payload = {
            "request_id": "fal_req_no_glb",
            "status": "OK",
            "payload": {
                "thumbnail": {
                    "url": "https://v3b.fal.media/files/preview.png"
                }
            },
        }

        response = self.client.post(self.webhook_url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        conversion.refresh_from_db()
        self.assertEqual(conversion.status, ConversionStatus.FAILED)
        self.assertEqual(
            conversion.error_message,
            "3D model generation completed but no GLB model URL was returned.",
        )
        mock_requests_get.assert_not_called()

    def test_fal_api_error_webhook_marks_conversion_as_failed(self):
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.PROCESSING,
            fal_request_id="fal_req_error_payload",
        )

        payload = {
            "request_id": "fal_req_error_payload",
            "status": "ERROR",
            "error": "Hunyuan 3D v3.1 Pro processing failed on fal.ai",
        }

        response = self.client.post(self.webhook_url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        conversion.refresh_from_db()
        self.assertEqual(conversion.status, ConversionStatus.FAILED)
        self.assertEqual(
            conversion.error_message, "3D model generation failed. Please try again later."
        )

    def test_detail_view_returns_result_mesh_url(self):
        fal_glb_url = "https://v3b.fal.media/files/b/0aa5dca1/Ute-7509uzk3GfGyJZGTL_combined_bodies.glb"
        conversion = ThreeDConversion.objects.create(
            outfit_job=self.completed_job_a,
            status=ConversionStatus.DONE,
            fal_request_id="fal_req_detail_test",
            result_mesh_url=fal_glb_url,
        )

        detail_url = reverse("model3d:conversion-detail", kwargs={"pk": conversion.id})
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["status"], "done")
        self.assertEqual(response.data["data"]["result_mesh_url"], fal_glb_url)

    def test_unknown_fal_request_id_webhook_returns_400(self):
        payload = {
            "request_id": "unknown_req_id_999",
            "status": "OK",
            "payload": {"model_glb": {"url": "https://fal.cdn/test.glb"}},
        }

        response = self.client.post(
            self.webhook_url,
            data=payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
