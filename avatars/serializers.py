import os
import uuid
from rest_framework import serializers
from .models import Avatar


class AvatarCreateSerializer(serializers.ModelSerializer):
    source_photo = serializers.ImageField(required=True)
    style = serializers.ChoiceField(
        choices=Avatar.Style.choices,
        default=Avatar.Style.REALISTIC,
        required=False,
    )

    class Meta:
        model = Avatar
        fields = ["id", "source_photo", "style"]
        read_only_fields = ["id"]

    def validate_source_photo(self, value):
        # The DB column for source_photo is varchar(100) and upload_to
        # prepends "avatars/source/" (15 chars), so long original filenames
        # (common from phone cameras/WhatsApp) can overflow it and raise a
        # DataError on save. Replace the name with a short unique one,
        # keeping the original extension.
        ext = os.path.splitext(value.name)[1].lower()
        value.name = f"{uuid.uuid4().hex}{ext}"
        return value

class SystemDefaultAvatarAdminSerializer(serializers.Serializer):
    gender = serializers.ChoiceField(choices=["male", "female"], required=True)
    style = serializers.ChoiceField(choices=Avatar.Style.choices, required=True)
    image = serializers.ImageField(required=True)

from drf_spectacular.utils import extend_schema_field


class AvatarSerializer(serializers.ModelSerializer):
    result_image = serializers.SerializerMethodField()

    class Meta:
        model = Avatar
        fields = [
            "id",
            "status",
            "gender",
            "style",
            "is_default",
            "is_preferred",
            "is_saved",
            "source_photo",
            "result_image",
            "fal_cdn_url",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_result_image(self, obj):
        if obj.is_default:
            if obj.result_image:
                try:
                    return obj.result_image.url
                except Exception:
                    pass
            return None
        if obj.is_saved and obj.result_image:
            try:
                return obj.result_image.url
            except Exception:
                pass
        return obj.fal_cdn_url or None

