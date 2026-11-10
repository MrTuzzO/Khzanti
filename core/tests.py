import io
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from avatars.models import Avatar
from core.models import BrandNew, SiteSettings
from core.validators import (
    RestrictedImageField,
    get_max_image_upload_size_bytes,
    get_max_image_upload_size_mb,
    validate_image_extension,
    validate_image_file_size,
)
from wardrobe.models import Category

User = get_user_model()


def _create_test_image_bytes(format="PNG", color="blue", size=(20, 20)):
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format)
    return buf.getvalue()


VALID_PNG = _create_test_image_bytes(format="PNG")
VALID_JPEG = _create_test_image_bytes(format="JPEG")
VALID_WEBP = _create_test_image_bytes(format="WEBP")

MOCK_CLOUDINARY = {
    "public_id": "test_public_id",
    "version": 1234567890,
    "width": 100,
    "height": 100,
    "format": "png",
    "resource_type": "image",
    "created_at": "2026-08-11T00:00:00Z",
    "bytes": 68,
    "type": "upload",
    "url": "http://res.cloudinary.com/test/image/upload/v1234567890/test.png",
    "secure_url": "https://res.cloudinary.com/test/image/upload/v1234567890/test.png",
}


class UploadSizeAndTypeValidatorUnitTestCase(APITestCase):
    """
    Unit tests for upload file size limits (below, at, and above 100 MB),
    supported image formats (JPEG, PNG, WebP), and invalid file types.
    """

    def test_file_size_below_100mb_passes(self):
        f = SimpleUploadedFile("photo.png", VALID_PNG, content_type="image/png")
        f.size = 50 * 1024 * 1024  # 50 MB
        res = validate_image_file_size(f)
        self.assertEqual(res, f)

    def test_file_size_exactly_at_100mb_passes(self):
        f = SimpleUploadedFile("photo.png", VALID_PNG, content_type="image/png")
        f.size = 100 * 1024 * 1024  # exactly 100 MB (104,857,600 bytes)
        res = validate_image_file_size(f)
        self.assertEqual(res, f)

    def test_file_size_above_100mb_fails(self):
        f = SimpleUploadedFile("photo.png", VALID_PNG, content_type="image/png")
        f.size = 100 * 1024 * 1024 + 1  # 100 MB + 1 byte
        with self.assertRaises(ValidationError) as ctx:
            validate_image_file_size(f)
        self.assertIn("exceeds the maximum allowed limit of 100 MB", str(ctx.exception))

    def test_configurable_upload_size_limit(self):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=10, MAX_IMAGE_UPLOAD_SIZE_BYTES=10 * 1024 * 1024):
            self.assertEqual(get_max_image_upload_size_mb(), 10)
            self.assertEqual(get_max_image_upload_size_bytes(), 10 * 1024 * 1024)

            # 10 MB exactly passes
            f_at = SimpleUploadedFile("photo.png", VALID_PNG, content_type="image/png")
            f_at.size = 10 * 1024 * 1024
            self.assertEqual(validate_image_file_size(f_at), f_at)

            # 10 MB + 1 byte fails
            f_over = SimpleUploadedFile("photo.png", VALID_PNG, content_type="image/png")
            f_over.size = 10 * 1024 * 1024 + 1
            with self.assertRaises(ValidationError) as ctx:
                validate_image_file_size(f_over)
            self.assertIn("exceeds the maximum allowed limit of 10 MB", str(ctx.exception))

    def test_supported_image_extensions_pass(self):
        for name in ["test.jpg", "test.jpeg", "test.png", "test.webp", "test.JPG", "test.PNG", "test.WEBP"]:
            f = SimpleUploadedFile(name, VALID_PNG, content_type="image/png")
            res = validate_image_extension(f)
            self.assertEqual(res, f)

    def test_unsupported_extensions_fail(self):
        for name in ["doc.txt", "doc.pdf", "app.exe", "vector.svg", "archive.zip"]:
            f = SimpleUploadedFile(name, b"dummy data", content_type="application/octet-stream")
            with self.assertRaises(ValidationError) as ctx:
                validate_image_extension(f)
            self.assertIn("Unsupported file extension", str(ctx.exception))

    def test_restricted_image_field_accepts_valid_formats(self):
        field = RestrictedImageField()
        for name, bytes_data, ctype in [
            ("test.png", VALID_PNG, "image/png"),
            ("test.jpg", VALID_JPEG, "image/jpeg"),
            ("test.jpeg", VALID_JPEG, "image/jpeg"),
            ("test.webp", VALID_WEBP, "image/webp"),
        ]:
            f = SimpleUploadedFile(name, bytes_data, content_type=ctype)
            val = field.run_validation(f)
            self.assertEqual(val.name, name)

    def test_restricted_image_field_rejects_above_100mb(self):
        field = RestrictedImageField()
        f = SimpleUploadedFile("large.png", VALID_PNG, content_type="image/png")
        f.size = 100 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError) as ctx:
            field.run_validation(f)
        self.assertIn("exceeds the maximum allowed limit of 100 MB", str(ctx.exception))

    def test_restricted_image_field_rejects_invalid_extension(self):
        field = RestrictedImageField()
        f = SimpleUploadedFile("payload.exe", b"binary", content_type="application/octet-stream")
        with self.assertRaises(ValidationError) as ctx:
            field.run_validation(f)
        self.assertIn("Unsupported file extension '.exe'", str(ctx.exception))

    def test_restricted_image_field_rejects_corrupted_image(self):
        field = RestrictedImageField()
        f = SimpleUploadedFile("corrupt.png", b"not an image at all", content_type="image/png")
        with self.assertRaises(ValidationError) as ctx:
            field.run_validation(f)
        self.assertIn("Upload a valid image", str(ctx.exception))


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY)
class UploadEndpointsIntegrationTestCase(APITestCase):
    """
    Integration tests verifying upload size enforcement, valid uploads, and error handling
    across all user-facing upload endpoints.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="uploader@example.com",
            name="Upload User",
            password="Password123!",
            username="uploader",
            is_email_verified=True,
        )
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Tops")

    # --- 1. Avatars Endpoint (POST /api/v1/avatars/) ---
    @patch("avatars.views.submit_avatar_job")
    def test_avatar_upload_valid_image_accepted(self, mock_submit, mock_cloud):
        f = SimpleUploadedFile("selfie.png", VALID_PNG, content_type="image/png")
        response = self.client.post("/api/v1/avatars/", {"source_photo": f, "style": "realistic"}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(mock_submit.called)

    @patch("avatars.views.submit_avatar_job")
    def test_avatar_upload_oversized_rejected_before_job(self, mock_submit, mock_cloud):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            # 2 MB file exceeds 1 MB limit
            oversized_content = b"x" * (2 * 1024 * 1024)
            f = SimpleUploadedFile("oversized.png", oversized_content, content_type="image/png")
            response = self.client.post("/api/v1/avatars/", {"source_photo": f, "style": "realistic"}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))
            self.assertFalse(mock_submit.called)

    @patch("avatars.views.submit_avatar_job")
    def test_avatar_upload_invalid_type_rejected(self, mock_submit, mock_cloud):
        f = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")
        response = self.client.post("/api/v1/avatars/", {"source_photo": f, "style": "realistic"}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(mock_submit.called)

    # --- 2. Wardrobe Items Endpoint (POST /api/v1/wardrobe-items-ai/items/) ---
    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async")
    def test_wardrobe_upload_valid_image_accepted(self, mock_bg, mock_cloud):
        f = SimpleUploadedFile("shirt.jpg", VALID_JPEG, content_type="image/jpeg")
        response = self.client.post(
            "/api/v1/wardrobe-items-ai/items/",
            {"image": f, "category": self.category.id, "season": "summer", "occasion": "casual"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async")
    def test_wardrobe_upload_oversized_rejected_before_job(self, mock_bg, mock_cloud):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            oversized_content = b"x" * (2 * 1024 * 1024)
            f = SimpleUploadedFile("shirt.jpg", oversized_content, content_type="image/jpeg")
            response = self.client.post(
                "/api/v1/wardrobe-items-ai/items/",
                {"image": f, "category": self.category.id, "season": "summer", "occasion": "casual"},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))
            self.assertFalse(mock_bg.called)

    @patch("wardrobe_items_ai.views.submit_bg_removal_job_async")
    def test_wardrobe_upload_invalid_type_rejected(self, mock_bg, mock_cloud):
        f = SimpleUploadedFile("document.pdf", b"%PDF-1.4...", content_type="application/pdf")
        response = self.client.post(
            "/api/v1/wardrobe-items-ai/items/",
            {"image": f, "category": self.category.id, "season": "summer", "occasion": "casual"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(mock_bg.called)

    # --- 3. Profile Image Update (PATCH /api/v1/auth/profile/) ---
    def test_profile_image_update_valid_accepted(self, mock_cloud):
        f = SimpleUploadedFile("profile.webp", VALID_WEBP, content_type="image/webp")
        response = self.client.patch("/api/v1/auth/profile/", {"profile_image": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_profile_image_update_oversized_rejected(self, mock_cloud):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            oversized_content = b"x" * (2 * 1024 * 1024)
            f = SimpleUploadedFile("profile.png", oversized_content, content_type="image/png")
            response = self.client.patch("/api/v1/auth/profile/", {"profile_image": f}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))

    def test_profile_image_update_invalid_type_rejected(self, mock_cloud):
        f = SimpleUploadedFile("script.sh", b"#!/bin/bash\necho 1", content_type="text/plain")
        response = self.client.patch("/api/v1/auth/profile/", {"profile_image": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- 4. Complete Profile Step (PATCH /api/v1/auth/profile/complete/) ---
    def test_complete_profile_image_oversized_rejected(self, mock_cloud):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            oversized_content = b"x" * (2 * 1024 * 1024)
            f = SimpleUploadedFile("profile.png", oversized_content, content_type="image/png")
            response = self.client.patch("/api/v1/auth/profile/complete/", {"profile_image": f}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))

    # --- 5. Feed Posts Endpoint (POST /api/v1/feed/posts/) ---
    def test_feed_post_valid_images_accepted(self, mock_cloud):
        f1 = SimpleUploadedFile("outfit1.png", VALID_PNG, content_type="image/png")
        f2 = SimpleUploadedFile("outfit2.jpg", VALID_JPEG, content_type="image/jpeg")
        response = self.client.post(
            "/api/v1/feed/posts/",
            {"caption": "Summer vibes", "privacy": "public", "uploaded_images": [f1, f2]},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_feed_post_oversized_image_rejected(self, mock_cloud):
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            f1 = SimpleUploadedFile("outfit1.png", VALID_PNG, content_type="image/png")
            f2 = SimpleUploadedFile("outfit2.png", b"x" * (2 * 1024 * 1024), content_type="image/png")
            response = self.client.post(
                "/api/v1/feed/posts/",
                {"caption": "Too big", "privacy": "public", "uploaded_images": [f1, f2]},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))

    def test_feed_post_invalid_type_rejected(self, mock_cloud):
        f = SimpleUploadedFile("bad.txt", b"not image", content_type="text/plain")
        response = self.client.post(
            "/api/v1/feed/posts/",
            {"caption": "Bad file", "privacy": "public", "uploaded_images": [f]},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- 6. Admin System Default Avatar (POST /api/v1/avatars/admin/defaults/) ---
    def test_admin_default_avatar_valid_accepted(self, mock_cloud):
        self.user.is_staff = True
        self.user.save()
        f = SimpleUploadedFile("default_male.png", VALID_PNG, content_type="image/png")
        response = self.client.post(
            "/api/v1/avatars/admin/defaults/",
            {"gender": "male", "style": "realistic", "image": f},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_default_avatar_oversized_rejected(self, mock_cloud):
        self.user.is_staff = True
        self.user.save()
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            f = SimpleUploadedFile("default_male.png", b"x" * (2 * 1024 * 1024), content_type="image/png")
            response = self.client.post(
                "/api/v1/avatars/admin/defaults/",
                {"gender": "male", "style": "realistic", "image": f},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("exceeds the maximum allowed limit", str(response.data))

    # --- 7. CKEditor Image Upload (/ckeditor5/image_upload/) ---
    def test_ckeditor_upload_oversized_rejected(self, mock_cloud):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        with override_settings(MAX_IMAGE_UPLOAD_SIZE_MB=1, MAX_IMAGE_UPLOAD_SIZE_BYTES=1024 * 1024):
            f = SimpleUploadedFile("article_pic.png", b"x" * (2 * 1024 * 1024), content_type="image/png")
            response = self.client.post(
                "/ckeditor5/image_upload/",
                {"upload": f},
                format="multipart",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("exceeds the maximum allowed limit", str(response.content))


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY)
class BrandNewAPITestCase(APITestCase):
    """
    Tests for Brand New REST API endpoints, validation rules, pagination,
    and preservation of core SiteSettings.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="brand_tester@example.com",
            name="Brand Tester",
            password="Password123!",
            username="brand_tester",
            is_email_verified=True,
        )
        self.list_url = "/api/v1/brand-new/"
        self.settings_url = "/api/v1/settings/"

    def test_list_brand_new_anonymous_empty(self, mock_upload):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertEqual(response.data["results"], [])

    def test_list_brand_new_pagination_structure_and_navigation(self, mock_upload):
        # Create 25 BrandNew items
        for i in range(25):
            BrandNew.objects.create(
                title=f"Brand Item {i:02d}",
                description=f"Description for item {i}",
                product_image="brand_new/products/item.png",
            )

        # First page: page_size defaults to 20
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])

        # Navigate to second page using the next URL
        next_page_url = response.data["next"]
        response_page2 = self.client.get(next_page_url)
        self.assertEqual(response_page2.status_code, status.HTTP_200_OK)
        self.assertEqual(response_page2.data["count"], 25)
        self.assertEqual(len(response_page2.data["results"]), 5)
        self.assertIsNone(response_page2.data["next"])
        self.assertIsNotNone(response_page2.data["previous"])

    def test_create_brand_new_unauthenticated_fails(self, mock_upload):
        f = SimpleUploadedFile("brand_product.png", VALID_PNG, content_type="image/png")
        payload = {
            "title": "Summer Denim Jacket",
            "description": "Premium oversized denim jacket.",
            "product_image": f,
        }
        response = self.client.post(self.list_url, payload, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_brand_new_authenticated_success(self, mock_upload):
        self.client.force_authenticate(user=self.user)
        f = SimpleUploadedFile("brand_product.png", VALID_PNG, content_type="image/png")
        payload = {
            "title": "  Summer Denim Jacket  ",
            "description": "  Premium oversized denim jacket.  ",
            "product_image": f,
        }
        response = self.client.post(self.list_url, payload, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Summer Denim Jacket")
        self.assertEqual(response.data["description"], "Premium oversized denim jacket.")
        self.assertIn("id", response.data)
        self.assertIn("created_at", response.data)
        self.assertIn("updated_at", response.data)
        self.assertTrue(BrandNew.objects.filter(title="Summer Denim Jacket").exists())

    def test_create_brand_new_title_validation(self, mock_upload):
        self.client.force_authenticate(user=self.user)

        # Blank or whitespace-only title
        f = SimpleUploadedFile("brand.png", VALID_PNG, content_type="image/png")
        response = self.client.post(
            self.list_url,
            {"title": "   ", "description": "Valid description", "product_image": f},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            "This field may not be blank." in response.data["message"]
            or "Title cannot be blank" in response.data["message"]
        )

        # Title shorter than 3 characters
        f2 = SimpleUploadedFile("brand.png", VALID_PNG, content_type="image/png")
        response2 = self.client.post(
            self.list_url,
            {"title": "ab", "description": "Valid description", "product_image": f2},
            format="multipart",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Title must be at least 3 characters long.", response2.data["message"])

    def test_create_brand_new_description_validation(self, mock_upload):
        self.client.force_authenticate(user=self.user)
        f = SimpleUploadedFile("brand.png", VALID_PNG, content_type="image/png")
        response = self.client.post(
            self.list_url,
            {"title": "Valid Title", "description": "   ", "product_image": f},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            "This field may not be blank." in response.data["message"]
            or "Description cannot be blank" in response.data["message"]
        )

    def test_create_brand_new_image_validation(self, mock_upload):
        self.client.force_authenticate(user=self.user)

        # Image exceeding 10 MB limit (valid PNG structure with > 10MB payload)
        large_bytes = VALID_PNG + (b"0" * (10 * 1024 * 1024 + 1))
        oversized = SimpleUploadedFile("huge.png", large_bytes, content_type="image/png")
        response = self.client.post(
            self.list_url,
            {"title": "Valid Title", "description": "Valid description", "product_image": oversized},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Image size cannot exceed 10MB.", response.data["message"])

        # Invalid file extension (valid image content with unsupported extension like .gif or .pdf)
        bad_ext = SimpleUploadedFile("photo.gif", VALID_PNG, content_type="image/gif")
        response2 = self.client.post(
            self.list_url,
            {"title": "Valid Title", "description": "Valid description", "product_image": bad_ext},
            format="multipart",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Only JPG, JPEG, PNG, or WEBP image formats are allowed.", response2.data["message"])

    def test_retrieve_brand_new_detail_and_not_found(self, mock_upload):
        item = BrandNew.objects.create(
            title="Vintage Boots",
            description="Classic leather boots.",
            product_image="brand_new/products/boots.png",
        )
        detail_url = f"/api/v1/brand-new/{item.pk}/"

        # Detail retrieve as anonymous
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], item.pk)
        self.assertEqual(response.data["title"], "Vintage Boots")
        self.assertEqual(response.data["description"], "Classic leather boots.")

        # Not found
        response_404 = self.client.get("/api/v1/brand-new/999999/")
        self.assertEqual(response_404.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_brand_new_put_and_patch(self, mock_upload):
        item = BrandNew.objects.create(
            title="Original Title",
            description="Original description",
            product_image="brand_new/products/orig.png",
        )
        detail_url = f"/api/v1/brand-new/{item.pk}/"

        # Unauthenticated PUT fails
        response = self.client.put(detail_url, {"title": "Updated"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Authenticated PATCH succeeds
        self.client.force_authenticate(user=self.user)
        response_patch = self.client.patch(detail_url, {"title": "Updated Brand Title"}, format="json")
        self.assertEqual(response_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(response_patch.data["title"], "Updated Brand Title")
        item.refresh_from_db()
        self.assertEqual(item.title, "Updated Brand Title")

        # Authenticated PUT with image succeeds
        new_img = SimpleUploadedFile("updated.jpg", VALID_JPEG, content_type="image/jpeg")
        response_put = self.client.put(
            detail_url,
            {"title": "Completely New Title", "description": "Completely new description", "product_image": new_img},
            format="multipart",
        )
        self.assertEqual(response_put.status_code, status.HTTP_200_OK)
        self.assertEqual(response_put.data["title"], "Completely New Title")

    def test_delete_brand_new(self, mock_upload):
        item = BrandNew.objects.create(
            title="To Be Deleted",
            description="Will be removed",
            product_image="brand_new/products/del.png",
        )
        detail_url = f"/api/v1/brand-new/{item.pk}/"

        # Unauthenticated DELETE fails
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertTrue(BrandNew.objects.filter(pk=item.pk).exists())

        # Authenticated DELETE succeeds
        self.client.force_authenticate(user=self.user)
        response_del = self.client.delete(detail_url)
        self.assertEqual(response_del.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BrandNew.objects.filter(pk=item.pk).exists())

    def test_site_settings_endpoint_preserved(self, mock_upload):
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
