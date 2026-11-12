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

    def test_male_profile_generates_gender_constraint_and_source_clothing_reconstruction(self, mock_cloud):
        profile_constraints = {"gender": "male"}
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, profile_constraints)

        self.assertIn("Gender Constraint: The subject is MALE", prompt)
        self.assertIn("AUTHORITATIVE CLOTHING RECONSTRUCTION & PARTIAL-IMAGE FALLBACK HIERARCHY", prompt)
        self.assertIn("PRIORITY 1 (COMPLETE SOURCE CLOTHING)", prompt)
        self.assertIn("MALE FALLBACK: When the source does not provide enough clothing information", prompt)
        self.assertIn("coat/jacket with tailored trousers/pants", prompt)

    def test_female_profile_generates_gender_constraint_and_source_clothing_reconstruction(self, mock_cloud):
        profile_constraints = {"gender": "female"}
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, profile_constraints)

        self.assertIn("Gender Constraint: The subject is FEMALE", prompt)
        self.assertIn("AUTHORITATIVE CLOTHING RECONSTRUCTION & PARTIAL-IMAGE FALLBACK HIERARCHY", prompt)
        self.assertIn("FEMALE FALLBACK: Do NOT generate a generic coat + pants suit", prompt)
        self.assertIn("modest, elegant, full-length kaftan or loose round long dress", prompt)
        self.assertIn("dominant/visible color or closely matching color family from the source image", prompt)

    def test_partial_source_clothing_fallbacks_and_color_matching(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})

        # 1. Partial male source -> fallback coat + trousers allowed
        self.assertIn("MALE FALLBACK: When the source does not provide enough clothing information to determine the full outfit, it is acceptable to generate a clean, polished coat/jacket with tailored trousers/pants", prompt)

        # 2. Partial female source -> fallback is long kaftan / loose round long dress
        self.assertIn("FEMALE FALLBACK: Do NOT generate a generic coat + pants suit, random Western suit, or abaya (unless the source actually indicates an abaya)", prompt)
        self.assertIn("generate a modest, elegant, full-length kaftan or loose round long dress that extends naturally to full length (head-to-toe)", prompt)

        # 3. Female fallback references visible source color / color family
        self.assertIn("purple/lavender upper garment -> purple/lavender long kaftan/dress", prompt)
        self.assertIn("blue -> blue long kaftan/dress", prompt)
        self.assertIn("beige/tan -> beige/tan long kaftan/dress", prompt)

        # 4. Clearly visible clothing still takes priority over fallback clothing
        self.assertIn("Clearly visible source clothing ALWAYS takes strict precedence over fallback templates", prompt)

    def test_mandatory_full_body_and_crop_independence(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})

        # 1. Reference not composition
        self.assertIn("PRIMARY IDENTITY & REFERENCE DIRECTIVE (Image 1 is a Reference, NOT a Composition Template)", prompt)
        self.assertIn("Do NOT reproduce the original image's camera framing, crop, background, photographic artifacts", prompt)

        # 2. Mandatory full-body head-to-toe
        self.assertIn("MANDATORY FULL-BODY AVATAR COMPOSITION (Head-to-Toe Framing)", prompt)
        self.assertIn("MANDATORY FULL-BODY REQUIREMENT", prompt)
        self.assertIn("The source image crop must NEVER determine the final avatar crop", prompt)
        self.assertIn("full head, neck, shoulders, torso, arms, hands, waist, hips, both legs, and feet visible in frame", prompt)

        # 3. Fallback for cropped / face-only / half-body
        self.assertIn("PRIORITY 3 (FALLBACK FOR CROPPED / FACE-ONLY / HALF-BODY SOURCE IMAGES)", prompt)

    def test_unspecified_gender_infers_from_person_reference(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})
        self.assertIn("Gender Inference: Apparent gender presentation must be directly inferred from the actual person in Image 1", prompt)
        self.assertIn("Infer gender from the person's physical features, not solely from clothing", prompt)

    def test_realistic_style_generates_premium_digital_human_avatar_prompt(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {"gender": "male"})
        self.assertIn("Premium Realistic 3D Digital Human Avatar", prompt)
        self.assertIn("Render as a premium realistic 3D digital human avatar", prompt)
        self.assertIn("NOT an unprocessed raw camera photograph", prompt)
        self.assertIn("Target: authentic human likeness + polished realistic 3D digital-avatar rendering", prompt)

    def test_prompt_prohibits_photo_filtering_and_retouching(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})
        self.assertIn("PROHIBITED: Do NOT apply a photo filter, beauty filter, airbrushing, or direct photographic edit to Image 1", prompt)
        self.assertIn("Do NOT modify, retouch, or paint over the original photograph", prompt)
        self.assertIn("Discard the original photograph's camera framing, crop, background, lighting, optical distortion, noise, photographic artifacts", prompt)

    def test_prompt_requires_standardized_upright_front_facing_pose_and_rejects_source_pose(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})
        self.assertIn("STANDARDIZED UPRIGHT POSE: The generated avatar MUST stand straight upright, front-facing directly towards the camera", prompt)
        self.assertIn("REJECT ORIGINAL POSE: Completely override and discard any pose present in Image 1", prompt)
        self.assertIn("head level, eyes looking forward, arms hanging relaxed naturally at the sides", prompt)

    def test_prompt_enforces_exact_outfit_preservation_and_prohibits_default_outfit_overwrite(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})
        self.assertIn("EXACT OUTFIT PRESERVATION: Reconstruct the exact outfit worn in Image 1 onto the new 3D avatar's body", prompt)
        self.assertIn("3D DIGITAL GARMENTS: The clothing must be modeled and rendered as high-quality 3D digital garments", prompt)
        self.assertIn("NO DEFAULT-AVATAR CLOTHING OVERWRITE: Strictly do NOT replace the user's uploaded outfit with default avatar clothing", prompt)

    def test_both_realistic_and_cartoon_styles_supported(self, mock_cloud):
        prompt_real = build_avatar_prompt(Avatar.Style.REALISTIC, {"gender": "male"})
        prompt_cart = build_avatar_prompt(Avatar.Style.CARTOON, {"gender": "female"})

        self.assertIn("Premium Realistic 3D Digital Human Avatar", prompt_real)
        self.assertIn("Polished 3D Cartoon / Stylized Character Avatar", prompt_cart)
        self.assertIn("high-fidelity 3D digital human character aesthetic", prompt_real)
        self.assertIn("Consistent artistic visual language", prompt_cart)

    def test_strengthened_full_body_composition_directives(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.REALISTIC, {})

        # Visibility requirements
        self.assertIn("Entire head visible", prompt)
        self.assertIn("Entire torso visible", prompt)
        self.assertIn("Both arms and hands visible", prompt)
        self.assertIn("Both legs visible", prompt)
        self.assertIn("Both feet completely visible", prompt)
        self.assertIn("Zero cropping: No cropping at the head, hands, knees, ankles, or feet", prompt)

        # Pose & framing
        self.assertIn("STANDARDIZED UPRIGHT POSE: The generated avatar MUST stand straight upright, front-facing directly towards the camera", prompt)
        self.assertIn("Full body centered within the image with adequate empty space above the head and below the feet", prompt)

        # Explicit prohibitions
        self.assertIn("EXPLICITLY PROHIBITED FRAMING: Bust shots, portrait close-ups, waist-up compositions, chest-up compositions, half-body framing", prompt)
        self.assertIn("cropping any part of the person's body are strictly forbidden", prompt)

    def test_composition_consistency_for_various_photo_types(self, mock_cloud):
        # 1. Full-body photo input
        # 2. Sitting photo input
        # 3. Angled selfie input
        # 4. Cropped upper-body photo input
        photo_types = ["full_body.png", "sitting_photo.png", "angled_selfie.png", "cropped_upper_body.png"]

        user = User.objects.create_user(
            email="varied_photos@example.com",
            name="Photo Test User",
            password="password123",
        )

        for photo_name in photo_types:
            dummy_file = SimpleUploadedFile(
                name=photo_name,
                content=VALID_PNG_BYTES,
                content_type="image/png",
            )
            avatar = Avatar.objects.create(
                user=user,
                source_photo=dummy_file,
                style=Avatar.Style.REALISTIC,
                status=Avatar.JobStatus.PENDING,
            )

            mock_handle = MagicMock()
            mock_handle.request_id = f"mock_{photo_name}"

            with patch("fal_client.submit", return_value=mock_handle) as mock_submit:
                submit_avatar_job(avatar)
                mock_submit.assert_called_once()
                args = mock_submit.call_args.kwargs.get("arguments", {})

                # Strict 9:16 aspect ratio configured
                self.assertEqual(args.get("aspect_ratio"), "9:16")
                self.assertEqual(args.get("resolution"), "1K")
                self.assertEqual(args.get("output_format"), "png")

                # Prompt enforces mandatory full body and rejects photo's pose/crop
                p = args.get("prompt", "")
                self.assertIn("MANDATORY FULL-BODY REQUIREMENT", p)
                self.assertIn("REJECT ORIGINAL POSE", p)
                self.assertIn("Both feet completely visible", p)
                self.assertIn("Full body centered within the image with adequate empty space", p)

    def test_cartoon_style_generates_stylized_art_prompt(self, mock_cloud):
        prompt = build_avatar_prompt(Avatar.Style.CARTOON, {"gender": "female"})
        self.assertIn("Polished 3D Cartoon / Stylized Character Avatar", prompt)
        self.assertIn("Consistent artistic visual language", prompt)
        self.assertIn("Render as a full-body polished 3D cartoon / stylized digital character avatar", prompt)
        self.assertIn("completely visible from the top of the head down to both feet", prompt)
        self.assertIn("MANDATORY FULL-BODY FRAMING: The entire stylized character MUST be visible head-to-toe", prompt)
        self.assertIn("STRICTLY PROHIBITED: Bust shots, chest-up illustrations, waist-up framing, half-body crops", prompt)
        self.assertIn("expressive 3D cartoon facial features", prompt)
        self.assertIn("clean silhouettes, smooth stylized shading", prompt)
        self.assertIn("Both feet completely visible", prompt)
        self.assertIn("STANDARDIZED UPRIGHT POSE", prompt)

    def test_cartoon_avatar_pipeline_submits_9_16_aspect_ratio_and_full_body_prompt(self, mock_cloud):
        user = User.objects.create_user(
            email="cartoon_user@example.com",
            name="Cartoon User",
            password="password123",
        )
        avatar = Avatar.objects.create(
            user=user,
            source_photo=self.dummy_image,
            style=Avatar.Style.CARTOON,
            status=Avatar.JobStatus.PENDING,
        )

        mock_handle = MagicMock()
        mock_handle.request_id = "mock_fal_cartoon_req"

        with patch("fal_client.submit", return_value=mock_handle) as mock_submit:
            submit_avatar_job(avatar)

            mock_submit.assert_called_once()
            args = mock_submit.call_args.kwargs.get("arguments", {})

            self.assertEqual(args.get("aspect_ratio"), "9:16")
            self.assertEqual(args.get("resolution"), "1K")
            self.assertEqual(args.get("output_format"), "png")

            p = args.get("prompt", "")
            self.assertIn("Polished 3D Cartoon / Stylized Character Avatar", p)
            self.assertIn("MANDATORY FULL-BODY REQUIREMENT", p)
            self.assertIn("Render as a full-body polished 3D cartoon", p)
            self.assertIn("STRICTLY PROHIBITED: Bust shots", p)
            self.assertIn("Both feet completely visible", p)
            self.assertIn("EXACT OUTFIT PRESERVATION", p)

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
            self.assertEqual(arguments.get("aspect_ratio"), "9:16")
            self.assertEqual(arguments.get("resolution"), "1K")
            self.assertEqual(arguments.get("output_format"), "png")

            prompt = arguments.get("prompt", "")
            self.assertIn("PRIMARY IDENTITY & REFERENCE DIRECTIVE", prompt)
            self.assertIn("MANDATORY FULL-BODY AVATAR COMPOSITION", prompt)
            self.assertIn("AUTHORITATIVE CLOTHING RECONSTRUCTION", prompt)
            self.assertIn("VISUAL STYLE & QUALITY STANDARD", prompt)
            self.assertIn("Gender Constraint: The subject is MALE", prompt)

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

    def test_sync_avatar_status_automatically_uploads_and_saves_to_cloudinary(self, mock_cloud):
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_sync_test_888",
        )
        mock_completed = fal_client.Completed(logs=[], metrics={})
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": "https://v3b.fal.media/test.png"}]},
        ), patch("requests.get", return_value=mock_resp):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertTrue(response.data["is_saved"])
        self.assertEqual(response.data["fal_cdn_url"], "https://v3b.fal.media/test.png")
        self.assertIn("res.cloudinary.com", response.data["result_image"])

        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)
        self.assertTrue(bool(avatar.result_image))

    def test_sync_avatar_status_failed_cloudinary_leaves_unsaved(self, mock_cloud):
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_sync_fail_999",
        )
        mock_completed = fal_client.Completed(logs=[], metrics={})

        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": "https://v3b.fal.media/test.png"}]},
        ), patch("requests.get", side_effect=Exception("Cloudinary upload failed")):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertFalse(response.data["is_saved"])

        avatar.refresh_from_db()
        self.assertFalse(avatar.is_saved)
        self.assertFalse(bool(avatar.result_image))

    def test_save_avatar_to_cloudinary_idempotent_and_prevents_simultaneous_uploads(self, mock_cloud):
        """Simultaneous calls to save_avatar_to_cloudinary perform upload only once."""
        from avatars.services import save_avatar_to_cloudinary
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/test_concurrent.png",
            is_saved=False,
        )
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            res1 = save_avatar_to_cloudinary(avatar)
            res2 = save_avatar_to_cloudinary(avatar)

        # GET was called exactly once because the second call detected is_saved
        self.assertEqual(mock_get.call_count, 1)
        self.assertTrue(res1.is_saved)
        self.assertTrue(res2.is_saved)
        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)

    def test_save_avatar_refuses_processing_or_pending_avatars(self, mock_cloud):
        """save_avatar_to_cloudinary refuses to process avatars in PENDING or PROCESSING state."""
        from avatars.services import save_avatar_to_cloudinary
        from core.exceptions import ServiceError

        for test_status in [Avatar.JobStatus.PENDING, Avatar.JobStatus.PROCESSING]:
            avatar = Avatar.objects.create(
                user=self.user,
                source_photo=self.dummy_image,
                style=Avatar.Style.REALISTIC,
                status=test_status,
                fal_cdn_url="https://v3b.fal.media/in_progress.png",
                is_saved=False,
            )
            with patch("requests.get") as mock_get, patch("outfits.services.trigger_today_auto_job_for_avatar") as mock_trigger:
                with self.assertRaises(ServiceError) as ctx:
                    save_avatar_to_cloudinary(avatar)
                self.assertIn("Avatar generation must be completed", str(ctx.exception.detail))
                mock_get.assert_not_called()
                mock_trigger.assert_not_called()

            avatar.refresh_from_db()
            self.assertFalse(avatar.is_saved)
            self.assertFalse(bool(avatar.result_image))

    def test_save_avatar_refuses_failed_avatars(self, mock_cloud):
        """save_avatar_to_cloudinary refuses to process avatars in FAILED state."""
        from avatars.services import save_avatar_to_cloudinary
        from core.exceptions import ServiceError

        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.FAILED,
            fal_cdn_url="https://v3b.fal.media/failed.png",
            is_saved=False,
            error_message="Generation failed on fal.ai",
        )
        with patch("requests.get") as mock_get, patch("outfits.services.trigger_today_auto_job_for_avatar") as mock_trigger:
            with self.assertRaises(ServiceError) as ctx:
                save_avatar_to_cloudinary(avatar)
            self.assertIn("Avatar generation must be completed", str(ctx.exception.detail))
            mock_get.assert_not_called()
            mock_trigger.assert_not_called()

        avatar.refresh_from_db()
        self.assertFalse(avatar.is_saved)
        self.assertFalse(bool(avatar.result_image))

    def test_save_avatar_refuses_empty_or_invalid_image_urls(self, mock_cloud):
        """save_avatar_to_cloudinary refuses avatars without a valid HTTP(S) image URL."""
        from avatars.services import save_avatar_to_cloudinary
        from core.exceptions import ServiceError

        for bad_url in ["", "   ", "not-a-url", "ftp://invalid/a.png"]:
            avatar = Avatar.objects.create(
                user=self.user,
                source_photo=self.dummy_image,
                style=Avatar.Style.REALISTIC,
                status=Avatar.JobStatus.DONE,
                fal_cdn_url=bad_url,
                is_saved=False,
            )
            with patch("requests.get") as mock_get:
                with self.assertRaises(ServiceError) as ctx:
                    save_avatar_to_cloudinary(avatar)
                self.assertIn("valid generated image", str(ctx.exception.detail))
                mock_get.assert_not_called()

            avatar.refresh_from_db()
            self.assertFalse(avatar.is_saved)

    def test_save_avatar_succeeds_for_completed_avatar_with_valid_url(self, mock_cloud):
        """save_avatar_to_cloudinary succeeds strictly for DONE avatars with valid result URLs."""
        from avatars.services import save_avatar_to_cloudinary

        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/valid_completed.png",
            is_saved=False,
        )
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get, patch(
            "outfits.services.trigger_today_auto_job_for_avatar"
        ) as mock_trigger:
            with self.captureOnCommitCallbacks(execute=True):
                saved = save_avatar_to_cloudinary(avatar)
            mock_get.assert_called_once_with("https://v3b.fal.media/valid_completed.png", timeout=30)
            mock_trigger.assert_called_once_with(avatar)

        self.assertTrue(saved.is_saved)
        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)
        self.assertTrue(bool(avatar.result_image))

    def test_avatar_url_transitions_to_cloudinary_url_and_persists_on_subsequent_requests(self, mock_cloud):
        """
        Tests complete URL transition:
        1. Creates an avatar in PROCESSING status with fal.ai request ID.
        2. Simulates successful fal.ai completion with fal CDN URL.
        3. Mocks Cloudinary upload returning a persistent Cloudinary URL.
        4. Calls avatar status endpoint and verifies result_image is the Cloudinary URL and is_saved=True.
        5. Calls avatar status endpoint again without fal.ai mocks to verify the Cloudinary URL was persisted.
        6. Calls avatar list endpoint to verify the returned avatar includes the Cloudinary URL.
        """
        fal_cdn_url = "https://v3b.fal.media/fal_generated_look_123.png"
        custom_cloudinary_response = {
            "public_id": "avatars/result/avatar_saved_auto",
            "version": 99999,
            "width": 100,
            "height": 100,
            "format": "png",
            "resource_type": "image",
            "created_at": "2026-08-11T00:00:00Z",
            "bytes": 68,
            "type": "upload",
            "url": "http://res.cloudinary.com/test/image/upload/v99999/avatar_saved_auto.png",
            "secure_url": "https://res.cloudinary.com/test/image/upload/v99999/avatar_saved_auto.png",
        }
        mock_cloud.return_value = custom_cloudinary_response

        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_transition_test_101",
        )

        mock_completed = fal_client.Completed(logs=[], metrics={})
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": fal_cdn_url}]},
        ), patch("requests.get", return_value=mock_resp):
            # First status call triggers completion & automatic Cloudinary saving
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertTrue(response.data["is_saved"])
        self.assertEqual(response.data["fal_cdn_url"], fal_cdn_url)
        # result_image must be the Cloudinary URL, NOT the fal CDN URL
        self.assertIn("res.cloudinary.com", response.data["result_image"])
        self.assertIn("avatar_saved_auto", response.data["result_image"])

        # Second status call (subsequent request): no fal.ai or Cloudinary mocks needed, reads from DB
        response2 = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data["status"], "done")
        self.assertTrue(response2.data["is_saved"])
        self.assertEqual(response2.data["result_image"], response.data["result_image"])
        self.assertEqual(response2.data["fal_cdn_url"], fal_cdn_url)

        # List endpoint also returns the Cloudinary URL
        list_response = self.client.get("/api/v1/avatars/mine/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        results = list_response.data if isinstance(list_response.data, list) else list_response.data.get("results", [])
        avatar_entry = next((item for item in results if item["id"] == avatar.id), None)
        self.assertIsNotNone(avatar_entry)
        self.assertTrue(avatar_entry["is_saved"])
        self.assertEqual(avatar_entry["result_image"], response.data["result_image"])

    def test_failed_cloudinary_upload_never_exposes_false_cloudinary_url_or_saved_flag(self, mock_cloud):
        """
        Verifies that when Cloudinary upload fails:
        - is_saved is False
        - result_image falls back to fal_cdn_url (or null), never exposes a false Cloudinary URL
        - Database record remains is_saved=False
        """
        fal_cdn_url = "https://v3b.fal.media/fal_cdn_only.png"
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_request_id="req_fail_cloud_102",
        )

        mock_completed = fal_client.Completed(logs=[], metrics={})

        with patch("fal_client.status", return_value=mock_completed), patch(
            "fal_client.result",
            return_value={"images": [{"url": fal_cdn_url}]},
        ), patch("requests.get", side_effect=Exception("Cloudinary upload failed")):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertFalse(response.data["is_saved"])
        # Must return the fal CDN URL, never a broken or false Cloudinary URL
        self.assertEqual(response.data["result_image"], fal_cdn_url)
        self.assertNotIn("cloudinary", response.data["result_image"].lower())

        avatar.refresh_from_db()
        self.assertFalse(avatar.is_saved)
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


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class SystemDefaultAvatarVerificationTestCase(APITestCase):
    def setUp(self):
        from accounts.models import CustomerProfile
        self.male_user = User.objects.create_user(
            email="male_default_test@example.com",
            name="Male Test User",
            password="password123",
        )
        p_male, _ = CustomerProfile.objects.get_or_create(user=self.male_user)
        p_male.gender = "male"
        p_male.save()

        self.female_user = User.objects.create_user(
            email="female_default_test@example.com",
            name="Female Test User",
            password="password123",
        )
        p_female, _ = CustomerProfile.objects.get_or_create(user=self.female_user)
        p_female.gender = "female"
        p_female.save()

    def test_default_avatar_fallback_creates_verified_cloudinary_path(self, mock_cloud):
        from avatars.services import resolve_user_default_avatar
        # When no default avatar exists in database
        Avatar.objects.filter(is_default=True).delete()

        av_cartoon = resolve_user_default_avatar(self.male_user, requested_style=Avatar.Style.CARTOON)
        self.assertNotEqual(av_cartoon.result_image.name, "avatars/result/default_avatar.png")
        self.assertEqual(
            av_cartoon.result_image.name,
            "media/avatars/result/system_default_male_cartoon_ecftdo",
        )

        av_female_real = resolve_user_default_avatar(self.female_user, requested_style=Avatar.Style.REALISTIC)
        self.assertEqual(
            av_female_real.result_image.name,
            "media/avatars/result/system_default_female_realistic_wfq4cl",
        )

    def test_stale_default_avatar_heals_automatically(self, mock_cloud):
        from avatars.services import resolve_user_default_avatar
        Avatar.objects.filter(is_default=True).delete()

        # Simulate legacy stale record with default_avatar.png
        stale = Avatar.objects.create(
            user=None,
            is_default=True,
            gender="female",
            style=Avatar.Style.CARTOON,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
            result_image="avatars/result/default_avatar.png",
        )

        healed = resolve_user_default_avatar(self.female_user, requested_style=Avatar.Style.CARTOON)
        self.assertEqual(healed.id, stale.id)
        self.assertEqual(
            healed.result_image.name,
            "media/avatars/result/system_default_female_cartoon_uh7xk3",
        )
        stale.refresh_from_db()
        self.assertEqual(
            stale.result_image.name,
            "media/avatars/result/system_default_female_cartoon_uh7xk3",
        )

    def test_default_endpoint_returns_200_and_valid_cloudinary_url(self, mock_cloud):
        from avatars.services import resolve_user_default_avatar
        Avatar.objects.filter(is_default=True).delete()

        self.client.force_authenticate(user=self.female_user)
        response = self.client.get("/api/v1/avatars/default/?style=cartoon")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_default"])
        self.assertEqual(response.data["style"], "cartoon")
        self.assertEqual(response.data["gender"], "female")
        self.assertIn("system_default_female_cartoon_uh7xk3", response.data["result_image"])

    def test_sync_default_avatars_management_command(self, mock_cloud):
        from django.core.management import call_command
        import io

        out = io.StringIO()
        call_command("sync_default_avatars", "--dry-run", stdout=out)
        output_str = out.getvalue()
        self.assertIn("=== Default Avatar Cloudinary & Database Audit ===", output_str)
        self.assertIn("DRY-RUN", output_str)

        out_run = io.StringIO()
        with patch("requests.get") as mock_req:
            mock_req.return_value.status_code = 200
            call_command("sync_default_avatars", stdout=out_run)

        # Verify all 4 system defaults exist in DB with expected public IDs
        for gender, style, expected_id in [
            ("male", Avatar.Style.REALISTIC, "media/avatars/result/system_default_male_realistic_zo4mva"),
            ("male", Avatar.Style.CARTOON, "media/avatars/result/system_default_male_cartoon_ecftdo"),
            ("female", Avatar.Style.REALISTIC, "media/avatars/result/system_default_female_realistic_wfq4cl"),
            ("female", Avatar.Style.CARTOON, "media/avatars/result/system_default_female_cartoon_uh7xk3"),
        ]:
            rec = Avatar.objects.filter(is_default=True, gender=gender, style=style).first()
            self.assertIsNotNone(rec, f"Missing default avatar for {gender} {style}")
            self.assertEqual(rec.result_image.name, expected_id)



@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class AvatarAutoSaveRetryTestCase(APITestCase):
    """Regression tests for the auto-save retry mechanism in sync_avatar_status."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="retrytestuser@example.com",
            name="Retry Test User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)
        self.dummy_image = SimpleUploadedFile(
            name="retry_selfie.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )

    def test_done_unsaved_avatar_retries_cloudinary_on_status_check(self, mock_cloud):
        """
        A DONE avatar with is_saved=False retries Cloudinary upload
        when sync_avatar_status is called, WITHOUT calling fal.ai again.
        """
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_request_id="req_done_unsaved_001",
            fal_cdn_url="https://v3b.fal.media/done_unsaved.png",
            is_saved=False,
        )

        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("fal_client.status") as mock_fal_status, \
             patch("fal_client.result") as mock_fal_result, \
             patch("requests.get", return_value=mock_resp):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertTrue(response.data["is_saved"])

        # Must NOT call fal.ai — the avatar is already DONE
        mock_fal_status.assert_not_called()
        mock_fal_result.assert_not_called()

        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)
        self.assertTrue(bool(avatar.result_image))
        self.assertIsNotNone(avatar.saved_at)

    def test_done_unsaved_avatar_retry_failure_keeps_unsaved(self, mock_cloud):
        """
        A DONE avatar with is_saved=False that fails Cloudinary retry
        remains DONE + unsaved (not FAILED).
        """
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_request_id="req_done_retry_fail_002",
            fal_cdn_url="https://v3b.fal.media/retry_fail.png",
            is_saved=False,
        )

        with patch("fal_client.status") as mock_fal_status, \
             patch("requests.get", side_effect=Exception("Cloudinary down")):
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "done")
        self.assertFalse(response.data["is_saved"])

        mock_fal_status.assert_not_called()

        avatar.refresh_from_db()
        self.assertEqual(avatar.status, Avatar.JobStatus.DONE)
        self.assertFalse(avatar.is_saved)

    def test_done_saved_avatar_does_not_retry(self, mock_cloud):
        """A DONE+saved avatar returns immediately without any external calls."""
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_request_id="req_done_saved_003",
            fal_cdn_url="https://v3b.fal.media/already_saved.png",
            is_saved=True,
            result_image="avatars/result/already_saved.png",
        )

        with patch("fal_client.status") as mock_fal_status, \
             patch("requests.get") as mock_get:
            response = self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_fal_status.assert_not_called()
        mock_get.assert_not_called()

    def test_repeated_status_checks_do_not_duplicate_uploads(self, mock_cloud):
        """Multiple rapid status checks only upload to Cloudinary once."""
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_request_id="req_rapid_004",
            fal_cdn_url="https://v3b.fal.media/rapid.png",
            is_saved=False,
        )

        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            self.client.get(f"/api/v1/avatars/{avatar.id}/status/")
            self.client.get(f"/api/v1/avatars/{avatar.id}/status/")
            self.client.get(f"/api/v1/avatars/{avatar.id}/status/")

        # Upload happens only once — subsequent calls see is_saved=True
        self.assertEqual(mock_get.call_count, 1)
        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)

    def test_resave_already_saved_avatar_updates_saved_at_and_triggers_regeneration(self, mock_cloud):
        """Re-saving an already-saved avatar updates saved_at and triggers Today's Look."""
        from avatars.services import save_avatar_to_cloudinary
        from django.utils import timezone
        import datetime

        old_time = timezone.now() - datetime.timedelta(days=3)
        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/resave.png",
            is_saved=True,
            saved_at=old_time,
            result_image="avatars/result/resave.png",
        )

        with patch("requests.get") as mock_get, \
             patch("outfits.services.trigger_today_auto_job_for_avatar") as mock_trigger:
            with self.captureOnCommitCallbacks(execute=True):
                result = save_avatar_to_cloudinary(avatar)

        # No Cloudinary re-upload
        mock_get.assert_not_called()
        # But regeneration IS triggered
        mock_trigger.assert_called_once()

        avatar.refresh_from_db()
        self.assertTrue(avatar.is_saved)
        self.assertGreater(avatar.saved_at, old_time)

    def test_serializer_output_does_not_expose_saved_at(self, mock_cloud):
        """AvatarSerializer must not expose saved_at — it's internal only."""
        from avatars.serializers import AvatarSerializer

        avatar = Avatar.objects.create(
            user=self.user,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/serial.png",
            is_saved=True,
            result_image="avatars/result/serial.png",
        )

        data = AvatarSerializer(avatar).data
        self.assertNotIn("saved_at", data)

        expected_fields = {
            "id", "status", "gender", "style", "is_default", "is_preferred",
            "is_saved", "source_photo", "result_image", "fal_cdn_url",
            "error_message", "created_at", "updated_at",
        }
        self.assertEqual(set(data.keys()), expected_fields)


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class RepairAvatarCloudinarySyncCommandTestCase(APITestCase):
    """Unit and regression tests for repair_avatar_cloudinary_sync management command."""

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.user_a = User.objects.create_user(
            email="repair_user_a@example.com",
            name="Repair User A",
            password="password123",
        )
        self.user_b = User.objects.create_user(
            email="repair_user_b@example.com",
            name="Repair User B",
            password="password123",
        )
        self.dummy_image = SimpleUploadedFile(
            name="test_repair.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )

    def test_dry_run_does_not_modify_database_or_upload(self, mock_cloud):
        from django.core.management import call_command
        import io

        av = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/dry_run.png",
            is_saved=False,
        )

        out = io.StringIO()
        with patch("requests.get") as mock_get, patch("requests.head") as mock_head:
            call_command("repair_avatar_cloudinary_sync", "--dry-run", stdout=out)
            mock_get.assert_not_called()
            mock_head.assert_not_called()

        av.refresh_from_db()
        self.assertFalse(av.is_saved)
        self.assertFalse(bool(av.result_image))
        self.assertIsNone(av.saved_at)

        output = out.getvalue()
        self.assertIn("Mode: DRY-RUN", output)
        self.assertIn(f"Avatar {av.id}", output)
        self.assertIn("Repaired assets:                  1", output)

    def test_live_repair_uploads_sets_saved_and_backfills_saved_at(self, mock_cloud):
        from django.core.management import call_command
        import io

        # 1. Avatar needing repair
        av1 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/repair_1.png",
            is_saved=False,
        )

        # 2. Already saved avatar with accessible Cloudinary asset but missing saved_at
        av2 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/repair_2.png",
            is_saved=True,
            result_image="avatars/result/repair_2.png",
            saved_at=None,
        )

        # 3. Incomplete avatar (should be skipped)
        av3 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.PROCESSING,
            fal_cdn_url="",
            is_saved=False,
        )

        # 4. Failed avatar (should be skipped)
        av4 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.FAILED,
            fal_cdn_url="https://v3b.fal.media/repair_failed.png",
            is_saved=False,
        )

        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200

        mock_get_resp = MagicMock()
        mock_get_resp.content = VALID_PNG_BYTES
        mock_get_resp.raise_for_status = MagicMock()

        out = io.StringIO()
        with patch("requests.head", return_value=mock_head_resp), \
             patch("requests.get", return_value=mock_get_resp) as mock_get, \
             patch("outfits.services.trigger_today_auto_job_for_avatar") as mock_trigger:
            call_command("repair_avatar_cloudinary_sync", stdout=out)

            # Never triggers Today's Look regeneration for historical records
            mock_trigger.assert_not_called()
            # Uploads only the unsaved avatar
            mock_get.assert_called_once_with("https://v3b.fal.media/repair_1.png", timeout=30)

        av1.refresh_from_db()
        self.assertTrue(av1.is_saved)
        self.assertTrue(bool(av1.result_image))
        self.assertEqual(av1.saved_at, av1.created_at)

        av2.refresh_from_db()
        self.assertTrue(av2.is_saved)
        self.assertEqual(av2.saved_at, av2.created_at)

        av3.refresh_from_db()
        self.assertFalse(av3.is_saved)

        av4.refresh_from_db()
        self.assertFalse(av4.is_saved)

        output = out.getvalue()
        self.assertIn("Repaired assets:                  1", output)
        self.assertIn("Backfilled timestamps:            1", output)
        self.assertIn("Skipped - Incomplete/In-flight:   1", output)
        self.assertIn("Skipped - Failed status:          1", output)

    def test_inaccessible_cloudinary_asset_recovers_from_fal_cdn(self, mock_cloud):
        """When Cloudinary returns 404 for an existing image, recover from fal_cdn_url."""
        from django.core.management import call_command
        import io

        av = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/recovery_source.png",
            is_saved=True,
            result_image="avatars/result/broken_cloud.png",
        )

        mock_head_404 = MagicMock()
        mock_head_404.status_code = 404

        mock_get_resp = MagicMock()
        mock_get_resp.content = VALID_PNG_BYTES
        mock_get_resp.raise_for_status = MagicMock()

        out = io.StringIO()
        with patch("requests.head", return_value=mock_head_404), \
             patch("requests.get", return_value=mock_get_resp) as mock_get:
            call_command("repair_avatar_cloudinary_sync", stdout=out)
            mock_get.assert_called_once_with("https://v3b.fal.media/recovery_source.png", timeout=30)

        av.refresh_from_db()
        self.assertTrue(av.is_saved)
        output = out.getvalue()
        self.assertIn("Inaccessible Cloudinary assets:   1", output)
        self.assertIn("Repaired assets:                  1", output)

    def test_inaccessible_cloudinary_without_fal_cdn_preserves_record(self, mock_cloud):
        """When Cloudinary returns 404 and fal_cdn_url is missing, preserve record and report."""
        from django.core.management import call_command
        import io

        av = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="",
            is_saved=True,
            result_image="avatars/result/broken_cloud_no_src.png",
        )

        mock_head_404 = MagicMock()
        mock_head_404.status_code = 404

        out = io.StringIO()
        with patch("requests.head", return_value=mock_head_404), \
             patch("requests.get") as mock_get:
            call_command("repair_avatar_cloudinary_sync", stdout=out)
            mock_get.assert_not_called()

        av.refresh_from_db()
        self.assertEqual(av.result_image.name, "avatars/result/broken_cloud_no_src.png")
        output = out.getvalue()
        self.assertIn("Inaccessible Cloudinary assets:   1", output)
        self.assertIn("Missing fal.ai sources:           1", output)
        self.assertIn("Repaired assets:                  0", output)

    def test_repair_command_is_idempotent_and_user_id_filtering(self, mock_cloud):
        from django.core.management import call_command
        import io

        av_a = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/user_a.png",
            is_saved=False,
        )
        av_b = Avatar.objects.create(
            user=self.user_b,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/user_b.png",
            is_saved=False,
        )

        mock_get_resp = MagicMock()
        mock_get_resp.content = VALID_PNG_BYTES
        mock_get_resp.raise_for_status = MagicMock()

        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200

        out = io.StringIO()
        # Filter strictly to user_a
        with patch("requests.get", return_value=mock_get_resp) as mock_get:
            call_command("repair_avatar_cloudinary_sync", f"--user-id={self.user_a.id}", stdout=out)
            self.assertEqual(mock_get.call_count, 1)

        av_a.refresh_from_db()
        av_b.refresh_from_db()
        self.assertTrue(av_a.is_saved)
        self.assertFalse(av_b.is_saved)

        # Running again for user_a verifies existing asset without re-downloading
        out2 = io.StringIO()
        with patch("requests.head", return_value=mock_head_resp), \
             patch("requests.get") as mock_get_second:
            call_command("repair_avatar_cloudinary_sync", f"--user-id={self.user_a.id}", stdout=out2)
            mock_get_second.assert_not_called()

        output2 = out2.getvalue()
        self.assertIn("Verified assets:                  1", output2)
        self.assertIn("Repaired assets:                  0", output2)

    def test_individual_failure_does_not_abort_subsequent_repairs(self, mock_cloud):
        import requests
        from django.core.management import call_command
        import io

        av1 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/broken.png",
            is_saved=False,
        )
        av2 = Avatar.objects.create(
            user=self.user_a,
            source_photo=self.dummy_image,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/working.png",
            is_saved=False,
        )

        def side_effect(url, **kwargs):
            if "broken" in url:
                raise requests.RequestException("404 Not Found")
            mock = MagicMock()
            mock.content = VALID_PNG_BYTES
            mock.raise_for_status = MagicMock()
            return mock

        out = io.StringIO()
        with patch("requests.get", side_effect=side_effect):
            call_command("repair_avatar_cloudinary_sync", stdout=out)

        av1.refresh_from_db()
        av2.refresh_from_db()
        self.assertFalse(av1.is_saved)
        self.assertTrue(av2.is_saved)

        output = out.getvalue()
        self.assertIn("Failures during repair:           1", output)
        self.assertIn("Repaired assets:                  1", output)
