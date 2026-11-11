import io
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, IntegrityError
from django.http import Http404
from django.test import RequestFactory, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotFound as DRFNotFound,
    PermissionDenied as DRFPermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.views import APIView

from avatars.models import Avatar
from core.exceptions import ServiceError
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
        self.assertIn("title", response.data)
        self.assertTrue(
            "This field may not be blank." in str(response.data["title"])
            or "Title cannot be blank" in str(response.data["title"])
        )

        # Title shorter than 3 characters
        f2 = SimpleUploadedFile("brand.png", VALID_PNG, content_type="image/png")
        response2 = self.client.post(
            self.list_url,
            {"title": "ab", "description": "Valid description", "product_image": f2},
            format="multipart",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", response2.data)
        self.assertIn("Title must be at least 3 characters long.", str(response2.data["title"]))

    def test_create_brand_new_description_validation(self, mock_upload):
        self.client.force_authenticate(user=self.user)
        f = SimpleUploadedFile("brand.png", VALID_PNG, content_type="image/png")
        response = self.client.post(
            self.list_url,
            {"title": "Valid Title", "description": "   ", "product_image": f},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("description", response.data)
        self.assertTrue(
            "This field may not be blank." in str(response.data["description"])
            or "Description cannot be blank" in str(response.data["description"])
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
        self.assertIn("product_image", response.data)
        self.assertIn("Image size cannot exceed 10MB.", str(response.data["product_image"]))

        # Invalid file extension (valid image content with unsupported extension like .gif or .pdf)
        bad_ext = SimpleUploadedFile("photo.gif", VALID_PNG, content_type="image/gif")
        response2 = self.client.post(
            self.list_url,
            {"title": "Valid Title", "description": "Valid description", "product_image": bad_ext},
            format="multipart",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_image", response2.data)
        self.assertIn("Only JPG, JPEG, PNG, or WEBP image formats are allowed.", str(response2.data["product_image"]))

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


class DummyExceptionView(APIView):
    """
    Test APIView used to verify global exception handling for various exception types.
    """
    def get(self, request, *args, **kwargs):
        error_type = request.query_params.get("error")
        if error_type == "drf_validation":
            raise ValidationError("Invalid request parameter.")
        if error_type == "drf_permission":
            raise DRFPermissionDenied("Custom DRF permission denied.")
        if error_type == "drf_not_found":
            raise DRFNotFound("Custom DRF resource not found.")
        if error_type == "drf_auth":
            raise AuthenticationFailed("Custom authentication failed.")
        if error_type == "django_404":
            raise Http404("Custom Django 404 resource missing.")
        if error_type == "django_permission":
            raise DjangoPermissionDenied("Custom Django permission denied.")
        if error_type == "service_error":
            raise ServiceError(detail="AI service temporarily offline.", status_code=502)
        if error_type == "integrity_unique":
            raise IntegrityError("duplicate key value violates unique constraint 'users_email_key'")
        if error_type == "integrity_other":
            raise IntegrityError("null value in column 'username' violates not-null constraint")
        if error_type == "database_error":
            raise DatabaseError("SELECT password, secret_key FROM accounts_user failed: connection refused")
        if error_type == "unexpected_python":
            1 / 0
        if error_type == "unexpected_sensitive_detail":
            class ArbitrarySensitiveError(Exception):
                detail = "SELECT password, api_key FROM accounts_user WHERE secret='123'"
            raise ArbitrarySensitiveError("Arbitrary failure")
        return Response({"status": "ok", "message": "all good"})


class GlobalExceptionHandlingTestCase(APITestCase):
    """
    Comprehensive test suite for centralized global exception handling across the project:
    1. DRF validation exceptions.
    2. Authentication exceptions.
    3. Permission exceptions.
    4. Missing objects and 404 errors.
    5. Unexpected Python exceptions.
    6. Serializer field-level validation errors.
    7. Django-level errors outside DRF.
    8. JSON responses for API errors.
    9. Normal HTML behavior for Django admin.
    10. Successful API responses remaining unchanged.
    11. No sensitive internal information exposed in production responses.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.view = DummyExceptionView.as_view()
        self.user = User.objects.create_user(
            email="global_exc_user@example.com",
            name="Global User",
            password="Password123!",
            username="global_exc_user",
            is_email_verified=True,
        )

    # 1. DRF validation exceptions
    def test_drf_validation_exception_format(self):
        req = self.factory.get("/api/v1/test/?error=drf_validation")
        resp = self.view(req)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data, {"detail": "Invalid request parameter."})
        self.assertNotIn("status", resp.data)
        self.assertNotIn("code", resp.data)

    # 2. Authentication exceptions
    def test_drf_authentication_exception_format(self):
        # Protected endpoint without auth credentials
        resp = self.client.post("/api/v1/brand-new/", {"title": "Test"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("detail", resp.data)
        self.assertEqual(resp.data["detail"], "Authentication credentials were not provided.")
        self.assertNotIn("status", resp.data)
        self.assertNotIn("code", resp.data)

        # Custom auth exception in view
        req = self.factory.get("/api/v1/test/?error=drf_auth")
        resp_custom = self.view(req)
        self.assertEqual(resp_custom.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(resp_custom.data["detail"], "Custom authentication failed.")

    # 3. Permission exceptions
    def test_drf_permission_exception_format(self):
        # DRF PermissionDenied
        req = self.factory.get("/api/v1/test/?error=drf_permission")
        resp = self.view(req)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["detail"], "Custom DRF permission denied.")
        self.assertNotIn("status", resp.data)

        # Django PermissionDenied converted by DRF
        req2 = self.factory.get("/api/v1/test/?error=django_permission")
        resp2 = self.view(req2)
        self.assertEqual(resp2.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp2.data["detail"], "Custom Django permission denied.")

    # 4. Missing objects and 404 errors
    def test_missing_objects_and_404_errors(self):
        # Detail view 404 on missing record
        resp = self.client.get("/api/v1/brand-new/999999/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", resp.data)
        self.assertIn("matches the given query", str(resp.data["detail"]))
        self.assertNotIn("status", resp.data)

        # DRF NotFound in view
        req = self.factory.get("/api/v1/test/?error=drf_not_found")
        resp2 = self.view(req)
        self.assertEqual(resp2.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp2.data["detail"], "Custom DRF resource not found.")

        # Django Http404 in view converted by DRF
        req3 = self.factory.get("/api/v1/test/?error=django_404")
        resp3 = self.view(req3)
        self.assertEqual(resp3.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp3.data["detail"], "Custom Django 404 resource missing.")

    # 5. Unexpected Python exceptions
    def test_unexpected_python_exception_returns_safe_500(self):
        with override_settings(DEBUG=False):
            req = self.factory.get("/api/v1/test/?error=unexpected_python")
            resp = self.view(req)
            self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertEqual(resp.data, {"detail": "An unexpected server error occurred."})
            self.assertNotIn("division by zero", str(resp.data).lower())

    # 6. Serializer field-level validation errors
    def test_serializer_field_level_validation_errors(self):
        self.client.force_authenticate(user=self.user)
        # Post missing required fields and short title
        resp = self.client.post(
            "/api/v1/brand-new/",
            {"title": "a", "description": ""},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # Preserves native field error structure: dict with field names as keys
        self.assertIn("title", resp.data)
        self.assertIn("description", resp.data)
        self.assertIn("product_image", resp.data)
        self.assertIsInstance(resp.data["title"], list)
        self.assertNotIn("status", resp.data)
        self.assertNotIn("code", resp.data)

    # 7. Django-level errors outside DRF
    def test_django_level_404_outside_drf_returns_json_for_api(self):
        # Non-existent endpoint under /api/v1/
        resp = self.client.get("/api/v1/non-existent-endpoint/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.headers.get("Content-Type"), "application/json")
        data = resp.json()
        self.assertEqual(data, {"detail": "Not found."})
        self.assertNotIn("status", data)

    # 8. JSON responses for API errors
    def test_api_errors_always_return_json_content_type(self):
        resp_401 = self.client.post("/api/v1/feed/posts/", {"caption": "test"})
        self.assertEqual(resp_401.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("application/json", resp_401.headers.get("Content-Type", ""))

    # 9. Normal HTML behavior for Django admin
    def test_django_admin_preserves_html_error_pages(self):
        # Authenticated admin user requesting non-existent admin page expecting HTML
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        resp = self.client.get("/admin/non-existent-admin-page/", HTTP_ACCEPT="text/html")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        content_type = resp.headers.get("Content-Type", "")
        self.assertIn("text/html", content_type)
        self.assertNotIn("application/json", content_type)

    # 10. Successful API responses remaining unchanged
    def test_successful_api_response_structure_unchanged(self):
        resp = self.client.get("/api/v1/settings/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Confirms native model/serializer dictionary, NOT wrapped in {"status": "success", "data": ...}
        self.assertIn("id", resp.data)
        self.assertNotIn("status", resp.data)
        self.assertNotIn("code", resp.data)

    # 11. No sensitive internal information exposed in production responses
    def test_no_sensitive_db_information_exposed(self):
        with override_settings(DEBUG=False):
            # DatabaseError with sensitive SQL query
            req = self.factory.get("/api/v1/test/?error=database_error")
            resp = self.view(req)
            self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertEqual(resp.data, {"detail": "A database error occurred."})
            # Ensure no credentials or queries leaked
            raw_text = str(resp.data)
            self.assertNotIn("password", raw_text.lower())
            self.assertNotIn("secret_key", raw_text.lower())
            self.assertNotIn("accounts_user", raw_text.lower())
            self.assertNotIn("select ", raw_text.lower())

    # 12. IntegrityError unique duplicate returns 409 Conflict
    def test_integrity_error_unique_duplicate_returns_409(self):
        req = self.factory.get("/api/v1/test/?error=integrity_unique")
        resp = self.view(req)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data, {"detail": "This record already exists or violates a unique constraint."})

    # 13. ServiceError preserves custom status code and detail message
    def test_custom_service_error_preserves_status_code_and_detail(self):
        req = self.factory.get("/api/v1/test/?error=service_error")
        resp = self.view(req)
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data, {"detail": "AI service temporarily offline."})

    # 14. Outside DRF handlers (handler400, handler403, handler404, handler500)
    def test_outside_drf_custom_handlers(self):
        from Khzanti.urls import api_handler400, api_handler403, api_handler404, api_handler500

        # API requests return safe native JSON without leaking exception internals
        api_req = self.factory.get("/api/v1/custom-test/")
        h400_resp = api_handler400(api_req, Exception("Internal resolver or syntax error."))
        self.assertEqual(h400_resp.status_code, 400)
        self.assertEqual(h400_resp.headers.get("Content-Type"), "application/json")
        self.assertEqual(h400_resp.content.decode("utf-8"), '{"detail": "Bad request."}')

        h403_resp = api_handler403(api_req, Exception("Access denied internal path."))
        self.assertEqual(h403_resp.status_code, 403)
        self.assertEqual(h403_resp.content.decode("utf-8"), '{"detail": "Permission denied."}')

        h404_resp = api_handler404(api_req, Exception("{'tried': [['accounts_urls']]}"))
        self.assertEqual(h404_resp.status_code, 404)
        self.assertEqual(h404_resp.content.decode("utf-8"), '{"detail": "Not found."}')

        h500_resp = api_handler500(api_req)
        self.assertEqual(h500_resp.status_code, 500)
        self.assertEqual(h500_resp.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

    # 15. ApiExceptionMiddleware unhandled exception handling and debug-mode sanitization
    def test_api_exception_middleware_intercepts_unhandled_error_and_sanitizes(self):
        from core.middleware import ApiExceptionMiddleware

        def dummy_view(request):
            return Response({"ok": True})

        middleware = ApiExceptionMiddleware(dummy_view)
        api_req = self.factory.get("/api/v1/some-endpoint/")

        # In production (DEBUG=False): generic unexpected message
        with override_settings(DEBUG=False):
            resp = middleware.process_exception(api_req, ValueError("Some error."))
            self.assertIsNotNone(resp)
            self.assertEqual(resp.status_code, 500)
            self.assertEqual(resp.headers.get("Content-Type"), "application/json")
            self.assertEqual(resp.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

        # In DEBUG=True: SQL, paths, passwords, and tokens are SANITIZED out
        with override_settings(DEBUG=True):
            # SQL leak attempt
            resp_sql = middleware.process_exception(api_req, ValueError("SELECT password FROM accounts_user"))
            self.assertEqual(resp_sql.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

            # Filesystem path leak attempt
            resp_path = middleware.process_exception(api_req, ValueError(r"Error in C:\Users\App\views.py line 42"))
            self.assertEqual(resp_path.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

            # Secret / Token leak attempt
            resp_token = middleware.process_exception(api_req, ValueError("Invalid token secret_key_12345"))
            self.assertEqual(resp_token.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

            # Safe application message in debug
            resp_safe = middleware.process_exception(api_req, ValueError("Calculation exceeded maximum item count"))
            self.assertEqual(resp_safe.content.decode("utf-8"), '{"detail": "Calculation exceeded maximum item count"}')

        # Non-API request returns None to allow standard Django HTML error view
        html_req = self.factory.get("/admin/some-endpoint/", HTTP_ACCEPT="text/html")
        html_resp = middleware.process_exception(html_req, ValueError("Admin failure."))
        self.assertIsNone(html_resp)

    # 16. Reliability of 409 duplicate detection across PostgreSQL and SQLite formats
    def test_integrity_error_duplicate_detection_postgres_and_sqlite(self):
        from core.exceptions import get_integrity_error_detail

        # PostgreSQL format
        pg_err = IntegrityError('duplicate key value violates unique constraint "users_email_key"\nDETAIL: Key (lower(email))=(test@example.com) already exists.')
        pg_code, pg_detail = get_integrity_error_detail(pg_err)
        self.assertEqual(pg_code, 409)
        self.assertEqual(pg_detail, "This record already exists or violates a unique constraint.")

        # SQLite format
        sqlite_err = IntegrityError("UNIQUE constraint failed: accounts_user.email")
        sq_code, sq_detail = get_integrity_error_detail(sqlite_err)
        self.assertEqual(sq_code, 409)
        self.assertEqual(sq_detail, "This record already exists or violates a unique constraint.")

        # Outfit specific duplicate format
        outfit_err = IntegrityError('duplicate key value violates unique constraint "saved_outfit_user_date_unique"')
        of_code, of_detail = get_integrity_error_detail(outfit_err)
        self.assertEqual(of_code, 409)
        self.assertEqual(of_detail, "This outfit has already been saved.")

        # Non-duplicate integrity error (foreign key or null constraint) -> 500
        null_err = IntegrityError('null value in column "username" violates not-null constraint')
        null_code, null_detail = get_integrity_error_detail(null_err)
        self.assertEqual(null_code, 500)
        self.assertEqual(null_detail, "A database error occurred.")

    # 17. Regression test: Arbitrary unexpected exception with sensitive .detail attribute is not trusted
    def test_unexpected_exception_with_sensitive_detail_is_sanitized(self):
        from core.middleware import ApiExceptionMiddleware

        # Inside DRF view: DEBUG=True must not leak sensitive .detail
        with override_settings(DEBUG=True):
            req_debug = self.factory.get("/api/v1/test/?error=unexpected_sensitive_detail")
            resp_debug = self.view(req_debug)
            self.assertEqual(resp_debug.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertEqual(resp_debug.data, {"detail": "An unexpected server error occurred."})
            raw_text = str(resp_debug.data).lower()
            self.assertNotIn("password", raw_text)
            self.assertNotIn("api_key", raw_text)
            self.assertNotIn("select ", raw_text)

        # Inside DRF view: DEBUG=False must return generic 500
        with override_settings(DEBUG=False):
            req_prod = self.factory.get("/api/v1/test/?error=unexpected_sensitive_detail")
            resp_prod = self.view(req_prod)
            self.assertEqual(resp_prod.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertEqual(resp_prod.data, {"detail": "An unexpected server error occurred."})

        # In middleware: Arbitrary exception with sensitive .detail attribute
        class CustomUntrustedError(Exception):
            detail = "Bearer secret_api_token_value_xyz"

        def dummy_view(request):
            return Response({"ok": True})

        middleware = ApiExceptionMiddleware(dummy_view)
        api_req = self.factory.get("/api/v1/some-endpoint/")

        with override_settings(DEBUG=True):
            mw_resp_debug = middleware.process_exception(api_req, CustomUntrustedError("Token error"))
            self.assertEqual(mw_resp_debug.status_code, 500)
            self.assertEqual(mw_resp_debug.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')

        with override_settings(DEBUG=False):
            mw_resp_prod = middleware.process_exception(api_req, CustomUntrustedError("Token error"))
            self.assertEqual(mw_resp_prod.status_code, 500)
            self.assertEqual(mw_resp_prod.content.decode("utf-8"), '{"detail": "An unexpected server error occurred."}')
