from rest_framework import serializers
from .models import BrandNew, SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = '__all__'


class BrandNewSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandNew
        fields = (
            "id",
            "product_image",
            "title",
            "description",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_title(self, value):
        stripped_value = value.strip()
        if not stripped_value:
            raise serializers.ValidationError("Title cannot be blank or contain only whitespace.")
        if len(stripped_value) < 3:
            raise serializers.ValidationError("Title must be at least 3 characters long.")
        return stripped_value

    def validate_description(self, value):
        stripped_value = value.strip()
        if not stripped_value:
            raise serializers.ValidationError("Description cannot be blank or contain only whitespace.")
        return stripped_value

    def validate_product_image(self, value):
        max_size_mb = 10
        if value.size > max_size_mb * 1024 * 1024:
            raise serializers.ValidationError(f"Image size cannot exceed {max_size_mb}MB.")

        valid_extensions = (".jpg", ".jpeg", ".png", ".webp")
        ext = str(value.name).lower()
        if not any(ext.endswith(valid_ext) for valid_ext in valid_extensions):
            raise serializers.ValidationError("Only JPG, JPEG, PNG, or WEBP image formats are allowed.")

        return value
