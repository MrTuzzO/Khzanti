import os
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

DEFAULT_MAX_IMAGE_UPLOAD_SIZE_MB = 100
DEFAULT_ALLOWED_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def get_max_image_upload_size_mb() -> int:
    return int(getattr(settings, "MAX_IMAGE_UPLOAD_SIZE_MB", DEFAULT_MAX_IMAGE_UPLOAD_SIZE_MB))


def get_max_image_upload_size_bytes() -> int:
    configured_bytes = getattr(settings, "MAX_IMAGE_UPLOAD_SIZE_BYTES", None)
    if configured_bytes is not None:
        return int(configured_bytes)
    return get_max_image_upload_size_mb() * 1024 * 1024


def get_allowed_image_extensions() -> tuple[str, ...]:
    allowed = getattr(settings, "ALLOWED_IMAGE_EXTENSIONS", DEFAULT_ALLOWED_IMAGE_EXTENSIONS)
    return tuple(e.lower().lstrip(".") for e in allowed)


def validate_image_file_size(value):
    """
    Validates that the uploaded file does not exceed the maximum allowed size.
    Avoids reading file content into memory by inspecting file.size.
    Raises serializers.ValidationError if size exceeds the limit.
    """
    if not value:
        return value

    max_size = get_max_image_upload_size_bytes()
    max_mb = get_max_image_upload_size_mb()

    size = getattr(value, "size", None)
    if size is not None and size > max_size:
        size_mb = size / (1024 * 1024)
        raise serializers.ValidationError(
            f"File size ({size_mb:.2f} MB) exceeds the maximum allowed limit of {max_mb} MB."
        )
    return value


def validate_image_extension(value):
    """
    Validates that the uploaded file has a supported image extension.
    Accepts JPEG (.jpg, .jpeg), PNG (.png), and WebP (.webp).
    """
    if not value or not hasattr(value, "name") or not value.name:
        return value

    allowed = get_allowed_image_extensions()
    _, ext = os.path.splitext(value.name)
    clean_ext = ext.lstrip(".").lower()
    if clean_ext not in allowed:
        allowed_str = ", ".join(f".{e}" for e in allowed)
        raise serializers.ValidationError(
            f"Unsupported file extension '{ext}'. Supported formats: {allowed_str}."
        )
    return value


class RestrictedImageField(serializers.ImageField):
    """
    Custom ImageField that validates:
    1. File size does not exceed the configured limit (before loading into memory/PIL).
    2. File extension is supported (JPEG, PNG, WebP).
    3. File is a valid, uncorrupted image (via standard DRF ImageField & Pillow verify).
    """
    default_error_messages = {
        **serializers.ImageField.default_error_messages,
        "size_limit": "File size ({size_mb:.2f} MB) exceeds the maximum allowed limit of {max_mb} MB.",
        "unsupported_extension": "Unsupported file extension '{ext}'. Supported formats: {allowed_str}.",
    }

    def __init__(self, *args, max_size_mb=None, allowed_extensions=None, **kwargs):
        self.custom_max_size_mb = max_size_mb
        self.custom_allowed_extensions = allowed_extensions
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        # 1. Early check on raw uploaded file size BEFORE Pillow processing
        # This prevents reading oversized payloads into memory.
        max_size = (
            self.custom_max_size_mb * 1024 * 1024
            if self.custom_max_size_mb is not None
            else get_max_image_upload_size_bytes()
        )
        max_mb = (
            self.custom_max_size_mb
            if self.custom_max_size_mb is not None
            else get_max_image_upload_size_mb()
        )

        size = getattr(data, "size", None)
        if size is not None and size > max_size:
            size_mb = size / (1024 * 1024)
            self.fail("size_limit", size_mb=size_mb, max_mb=max_mb)

        # 2. Check extension if name exists
        if hasattr(data, "name") and data.name:
            allowed = (
                tuple(e.lower().lstrip(".") for e in self.custom_allowed_extensions)
                if self.custom_allowed_extensions is not None
                else get_allowed_image_extensions()
            )
            _, ext = os.path.splitext(data.name)
            clean_ext = ext.lstrip(".").lower()
            if clean_ext not in allowed:
                allowed_str = ", ".join(f".{e}" for e in allowed)
                self.fail("unsupported_extension", ext=ext, allowed_str=allowed_str)

        # 3. Standard DRF validation: checks allow_empty_file, Pillow open & verify
        try:
            return super().to_internal_value(data)
        except DjangoValidationError:
            self.fail("invalid_image")
