from rest_framework import serializers
from .models import ThreeDConversion


class ConvertRequestSerializer(serializers.Serializer):
    outfit_job_id = serializers.IntegerField(
        help_text="ID of the completed OutfitJob to convert into 3D."
    )


class ThreeDConversionSerializer(serializers.ModelSerializer):
    outfit_job = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ThreeDConversion
        fields = [
            "id",
            "outfit_job",
            "status",
            "fal_request_id",
            "result_mesh_url",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
