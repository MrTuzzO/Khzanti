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



class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Avatar
        fields = [
            "id",
            "status",
            "style",
            "is_default",
            "source_photo",
            "result_image",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

