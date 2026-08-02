from rest_framework import serializers
from .models import Avatar


class AvatarCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Avatar
        fields = ["id", "source_photo"]
        read_only_fields = ["id"]


class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = Avatar
        fields = [
            "id",
            "status",
            "source_photo",
            "result_image",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
