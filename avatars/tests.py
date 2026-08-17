from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from avatars.adapters import get_profile_constraints
from avatars.models import Avatar
from avatars.services import build_avatar_prompt, submit_avatar_job

User = get_user_model()

import io
from PIL import Image

def generate_test_image_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(buf, format="PNG")
    return buf.getvalue()

VALID_PNG_BYTES = generate_test_image_bytes()

MOCK_CLOUDINARY_RESPONSE = {
    "public_id": "avatars/source/test_selfie",
    "version": 1234567890,
    "width": 100,
    "height": 100,
    "format": "png",
    "resource_type": "image",
    "created_at": "2026-08-11T00:00:00Z",
    "bytes": 68,
    "type": "upload",
    "url": "http://res.cloudinary.com/test/image/upload/v1234567890/test_selfie.png",
    "secure_url": "https://res.cloudinary.com/test/image/upload/v1234567890/test_selfie.png",
}


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class AvatarPromptAndPipelineTestCase(APITestCase):
    def setUp(self):
        self.dummy_image = SimpleUploadedFile(
            name="test_selfie.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )

    def test_male_profile_generates_black_formal_suit_clothing(self, mock_cloud):
        profile_constraints = {"gender": "male"}
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, profile_constraints)

        self.assertIn("black formal suit", prompt)
        self.assertIn("formal shirt", prompt)
        self.assertIn("professional tailored trousers", prompt)
        self.assertIn("Avoid casual T-shirts", prompt)

    def test_female_profile_generates_modest_nontight_clothing(self, mock_cloud):
        profile_constraints = {"gender": "female"}
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, profile_constraints)

        self.assertIn("Saudi/Arabian corporate environment", prompt)
        self.assertIn("long, loose-fitting formal dress or abaya-style", prompt)
        self.assertIn("full-length sleeves", prompt)
        self.assertIn("high covered neckline", prompt)
        self.assertIn("loose, non-body-hugging silhouette", prompt)
        self.assertIn("Do NOT automatically add a headscarf or hijab", prompt)

    def test_profile_information_incorporated_into_prompt(self, mock_cloud):
        profile_constraints = {
            "gender": "male",
            "height": "182 cm",
            "age": 29,
            "body_type": "athletic",
            "weight": "78 kg",
        }
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, profile_constraints)

        self.assertIn("Gender: male", prompt)
        self.assertIn("Height: 182 cm", prompt)
        self.assertIn("Age: 29", prompt)
        self.assertIn("Body build/type: athletic", prompt)
        self.assertIn("Weight: 78 kg", prompt)

    def test_reference_image_and_prompt_passed_to_pipeline(self, mock_cloud):
        user = User.objects.create_user(
            email="testuser@example.com",
            name="Test User",
            password="password123",
        )
        user.gender = "male"
        user.height = "180cm"

        avatar = Avatar.objects.create(
            user=user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PENDING,
        )

        mock_handle = MagicMock()
        mock_handle.request_id = "mock_fal_req_12345"

        with patch("fal_client.submit", return_value=mock_handle) as mock_submit:
            submit_avatar_job(avatar)

            mock_submit.assert_called_once()
            call_kwargs = mock_submit.call_args.kwargs
            arguments = call_kwargs.get("arguments", {})

            self.assertIn("image_urls", arguments)
            self.assertEqual(len(arguments["image_urls"]), 1)

            prompt = arguments.get("prompt", "")
            self.assertIn("Identity & Physical Characteristics", prompt)
            self.assertIn("Body & Proportions", prompt)
            self.assertIn("Appearance", prompt)
            self.assertIn("Default Clothing", prompt)
            self.assertIn("black formal suit", prompt)

        avatar.refresh_from_db()
        self.assertEqual(avatar.status, Avatar.JobStatus.PROCESSING)
        self.assertEqual(avatar.fal_request_id, "mock_fal_req_12345")


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class AvatarAPIEndpointsTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="apiuser@example.com",
            name="API User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)
        self.dummy_image = SimpleUploadedFile(
            name="user_selfie.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )

    def test_avatar_create_api_flow(self, mock_cloud):
        mock_handle = MagicMock()
        mock_handle.request_id = "req_api_test_777"

        with patch("fal_client.submit", return_value=mock_handle):
            response = self.client.post(
                "/api/v1/avatars/",
                {"source_photo": self.dummy_image, "style": "realistic"},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "processing")
        self.assertIn("id", response.data)

    def test_avatar_list_mine(self, mock_cloud):
        Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
        )
        response = self.client.get("/api/v1/avatars/mine/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_avatar_default_view(self, mock_cloud):
        response = self.client.get("/api/v1/avatars/default/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_default"])

    @patch("requests.get")
    def test_avatar_save_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_response = MagicMock()
        mock_response.content = VALID_PNG_BYTES
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar_test.png",
            is_saved=False,
        )

        # 1. Verify initially unsaved & returns fal.ai URL
        status_resp = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")
        self.assertEqual(status_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp.data["result_image"], "https://v3b.fal.media/avatar_test.png")
        self.assertFalse(status_resp.data["saved"])

        # 2. Save avatar (uploads to Cloudinary)
        save_resp = self.client.post(f"/api/v1/avatars/{avatar.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(save_resp.data["saved"])
        self.assertIn("http", save_resp.data["result_image"])

        mock_requests_get.assert_called_once()
        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)

        # 3. Duplicate save call (must be idempotent, no second HTTP download)
        save_resp_2 = self.client.post(f"/api/v1/avatars/{avatar.id}/save/")
        self.assertEqual(save_resp_2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_requests_get.call_count, 1)

    def test_user_cannot_save_other_user_avatar(self, mock_cloud):
        other_user = User.objects.create_user(
            email="otheruser@example.com",
            name="Other User",
            password="password123",
        )
        other_avatar = Avatar.objects.create(
            user=other_user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other_avatar.png",
            is_saved=False,
        )

        response = self.client.post(f"/api/v1/avatars/{other_avatar.id}/save/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
