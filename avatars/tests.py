import fal_client
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
        from accounts.models import CustomerProfile

        # Setup 4 system default avatars
        sys_male_real = Avatar.objects.create(
            user=None, is_default=True, gender="male", style="realistic",
            status=Avatar.JobStatus.DONE, is_saved=True, result_image="avatars/result/def_m_r.png"
        )
        sys_male_cart = Avatar.objects.create(
            user=None, is_default=True, gender="male", style="cartoon",
            status=Avatar.JobStatus.DONE, is_saved=True, result_image="avatars/result/def_m_c.png"
        )
        sys_fem_real = Avatar.objects.create(
            user=None, is_default=True, gender="female", style="realistic",
            status=Avatar.JobStatus.DONE, is_saved=True, result_image="avatars/result/def_f_r.png"
        )
        sys_fem_cart = Avatar.objects.create(
            user=None, is_default=True, gender="female", style="cartoon",
            status=Avatar.JobStatus.DONE, is_saved=True, result_image="avatars/result/def_f_c.png"
        )

        profile, _ = CustomerProfile.objects.get_or_create(user=self.user)
        profile.gender = "male"
        profile.save()

        # 6. No user avatar: ?style=realistic -> gender-matched realistic system default; ?style=cartoon -> cartoon system default
        res_no_user_real = self.client.get("/api/v1/avatars/default/?style=realistic")
        self.assertEqual(res_no_user_real.status_code, status.HTTP_200_OK)
        self.assertEqual(res_no_user_real.data["id"], sys_male_real.id)
        self.assertEqual(res_no_user_real.data["style"], "realistic")
        self.assertTrue(res_no_user_real.data["is_default"])

        res_no_user_cart = self.client.get("/api/v1/avatars/default/?style=cartoon")
        self.assertEqual(res_no_user_cart.status_code, status.HTTP_200_OK)
        self.assertEqual(res_no_user_cart.data["id"], sys_male_cart.id)
        self.assertEqual(res_no_user_cart.data["style"], "cartoon")
        self.assertTrue(res_no_user_cart.data["is_default"])

        # 1. Preferred realistic avatar + ?style=realistic -> returns realistic user avatar
        avatar_a = Avatar.objects.create(
            user=self.user,
            style="realistic",
            status=Avatar.JobStatus.DONE,
            is_preferred=True,
            fal_cdn_url="https://v3b.fal.media/avatar_a.png",
        )
        res_real_pref = self.client.get("/api/v1/avatars/default/?style=realistic")
        self.assertEqual(res_real_pref.status_code, status.HTTP_200_OK)
        self.assertEqual(res_real_pref.data["id"], avatar_a.id)
        self.assertEqual(res_real_pref.data["style"], "realistic")

        # 2. Preferred realistic avatar + ?style=cartoon -> DOES NOT return realistic avatar; returns cartoon system default
        res_real_pref_cart_req = self.client.get("/api/v1/avatars/default/?style=cartoon")
        self.assertEqual(res_real_pref_cart_req.status_code, status.HTTP_200_OK)
        self.assertNotEqual(res_real_pref_cart_req.data["id"], avatar_a.id)
        self.assertEqual(res_real_pref_cart_req.data["id"], sys_male_cart.id)
        self.assertEqual(res_real_pref_cart_req.data["style"], "cartoon")

        # 3. Preferred cartoon avatar + ?style=cartoon -> returns cartoon user avatar
        avatar_a.is_preferred = False
        avatar_a.save()
        avatar_b = Avatar.objects.create(
            user=self.user,
            style="cartoon",
            status=Avatar.JobStatus.DONE,
            is_preferred=True,
            fal_cdn_url="https://v3b.fal.media/avatar_b.png",
        )
        res_cart_pref = self.client.get("/api/v1/avatars/default/?style=cartoon")
        self.assertEqual(res_cart_pref.status_code, status.HTTP_200_OK)
        self.assertEqual(res_cart_pref.data["id"], avatar_b.id)
        self.assertEqual(res_cart_pref.data["style"], "cartoon")

        # 4. Preferred cartoon avatar + ?style=realistic -> does not return cartoon avatar; returns latest completed realistic user avatar (avatar_a)
        res_cart_pref_real_req = self.client.get("/api/v1/avatars/default/?style=realistic")
        self.assertEqual(res_cart_pref_real_req.status_code, status.HTTP_200_OK)
        self.assertEqual(res_cart_pref_real_req.data["id"], avatar_a.id)
        self.assertEqual(res_cart_pref_real_req.data["style"], "realistic")

        # 5. Multiple user avatars: realistic A, cartoon B, realistic C -> ?style=realistic returns C, ?style=cartoon returns B
        Avatar.objects.filter(user=self.user).update(is_preferred=False)
        avatar_c = Avatar.objects.create(
            user=self.user,
            style="realistic",
            status=Avatar.JobStatus.DONE,
            is_preferred=True,
            fal_cdn_url="https://v3b.fal.media/avatar_c.png",
        )

        res_multi_real = self.client.get("/api/v1/avatars/default/?style=realistic")
        self.assertEqual(res_multi_real.status_code, status.HTTP_200_OK)
        self.assertEqual(res_multi_real.data["id"], avatar_c.id)
        self.assertEqual(res_multi_real.data["style"], "realistic")

        res_multi_cart = self.client.get("/api/v1/avatars/default/?style=cartoon")
        self.assertEqual(res_multi_cart.status_code, status.HTTP_200_OK)
        self.assertEqual(res_multi_cart.data["id"], avatar_b.id)
        self.assertEqual(res_multi_cart.data["style"], "cartoon")

        # 7. Verify the returned avatar's "style" ALWAYS equals the requested ?style
        self.assertEqual(res_no_user_real.data["style"], "realistic")
        self.assertEqual(res_no_user_cart.data["style"], "cartoon")
        self.assertEqual(res_real_pref.data["style"], "realistic")
        self.assertEqual(res_real_pref_cart_req.data["style"], "cartoon")
        self.assertEqual(res_cart_pref.data["style"], "cartoon")
        self.assertEqual(res_cart_pref_real_req.data["style"], "realistic")
        self.assertEqual(res_multi_real.data["style"], "realistic")
        self.assertEqual(res_multi_cart.data["style"], "cartoon")

    def test_sync_avatar_status_leaves_unsaved_fal_cdn_url(self, mock_cloud):
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_sync_test_888",
        )
        mock_completed = fal_client.Completed(logs=[], metrics={})

        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": "https://v3b.fal.media/test.png"}]},
        ):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertFalse(response.data["is_saved"])
        self.assertEqual(response.data["fal_cdn_url"], "https://v3b.fal.media/test.png")
        self.assertEqual(response.data["result_image"], "https://v3b.fal.media/test.png")

        avatar.refresh_from_db()
        self.assertFalse(bool(avatar.result_image))

    def test_sync_avatar_status_marks_is_preferred(self, mock_cloud):
        avatar_a = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_pref_a",
        )
        mock_completed = fal_client.Completed(logs=[], metrics={})
        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": "https://v3b.fal.media/a.png"}]},
        ):
            self.client.get(f"/api/v1/avatars/{avatar_a.id}/status/")

        avatar_a.refresh_from_db()
        self.assertTrue(avatar_a.is_preferred)
        self.assertFalse(avatar_a.is_saved)

        avatar_b = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.CARTOON,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_pref_b",
        )
        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": "https://v3b.fal.media/b.png"}]},
        ):
            self.client.get(f"/api/v1/avatars/{avatar_b.id}/status/")

        avatar_a.refresh_from_db()
        avatar_b.refresh_from_db()
        self.assertFalse(avatar_a.is_preferred)
        self.assertTrue(avatar_b.is_preferred)

    def test_system_defaults_admin_endpoint(self, mock_cloud):
        def _get_file():
            return SimpleUploadedFile(name="default_img.png", content=VALID_PNG_BYTES, content_type="image/png")

        # Non-admin rejected for both GET and POST
        response_user = self.client.post(
            "/api/v1/avatars/admin/defaults/",
            {"gender": "male", "style": "realistic", "image": _get_file()},
            format="multipart",
        )
        self.assertEqual(response_user.status_code, status.HTTP_403_FORBIDDEN)

        response_user_get = self.client.get("/api/v1/avatars/admin/defaults/")
        self.assertEqual(response_user_get.status_code, status.HTTP_403_FORBIDDEN)

        # Admin user allowed
        admin_user = User.objects.create_superuser(
            email="admin@example.com", name="Admin User", password="password123"
        )
        self.client.force_authenticate(user=admin_user)
        response_admin = self.client.post(
            "/api/v1/avatars/admin/defaults/",
            {"gender": "male", "style": "realistic", "image": _get_file()},
            format="multipart",
        )
        self.assertEqual(response_admin.status_code, status.HTTP_200_OK, response_admin.data)
        self.assertTrue(response_admin.data["is_default"])
        self.assertEqual(response_admin.data["gender"], "male")
        self.assertEqual(response_admin.data["style"], "realistic")

        # GET returns configured system defaults list for admin
        response_admin_get = self.client.get("/api/v1/avatars/admin/defaults/")
        self.assertEqual(response_admin_get.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_admin_get.data), 1)

        # Second admin upload replaces existing default for male+realistic
        response_admin_replace = self.client.post(
            "/api/v1/avatars/admin/defaults/",
            {"gender": "male", "style": "realistic", "image": _get_file()},
            format="multipart",
        )
        self.assertEqual(response_admin_replace.status_code, status.HTTP_200_OK)
        self.assertEqual(Avatar.objects.filter(is_default=True, gender="male", style="realistic").count(), 1)

    def test_tryon_avatar_priority_and_explicit_avatar_id(self, mock_cloud):
        from outfits.services import resolve_tryon_avatar

        # 1. System default fallback
        Avatar.objects.create(
            user=None,
            is_default=True,
            gender="male",
            style="realistic",
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_male_realistic.png",
        )
        resolved_default = resolve_tryon_avatar(self.user, requested_style="realistic")
        self.assertTrue(resolved_default.is_default)

        # 2. Preferred avatar priority
        pref_avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/pref.png",
            is_preferred=True,
        )
        resolved_pref = resolve_tryon_avatar(self.user, requested_style="realistic")
        self.assertEqual(resolved_pref.id, pref_avatar.id)
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/test.png",
            is_saved=False,
        )

        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            response = self.client.post(f"/api/v1/avatars/{avatar.id}/save/")

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(response.data["is_saved"])
            self.assertIn("res.cloudinary.com", response.data["result_image"])
            mock_get.assert_called_once_with("https://v3b.fal.media/test.png", timeout=30)

        # Idempotent second call should not call requests.get again
        with patch("requests.get") as mock_get_second:
            response_second = self.client.post(f"/api/v1/avatars/{avatar.id}/save/")
            self.assertEqual(response_second.status_code, status.HTTP_200_OK)
            self.assertTrue(response_second.data["is_saved"])
            mock_get_second.assert_not_called()

    def test_avatar_save_rejects_unowned_or_default_avatar(self, mock_cloud):
        default_avatar = Avatar.objects.create(
            user=None,
            is_default=True,
            status=Avatar.JobStatus.DONE,
            style=Avatar.Style.REALISTIC,
            result_image="avatars/result/default_avatar.png",
        )
        response = self.client.post(f"/api/v1/avatars/{default_avatar.id}/save/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
            fal_cdn_url="https://v3b.fal.media/other.png",
        )
        response_other = self.client.post(f"/api/v1/avatars/{other_avatar.id}/save/")
        self.assertEqual(response_other.status_code, status.HTTP_404_NOT_FOUND)
