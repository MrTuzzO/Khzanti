from rest_framework import serializers
from .models import ItemAnalysis


class ItemAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemAnalysis
        fields = [
            "id",
            "wardrobe_item",
            "status",
            "processed_image",
            "color",
            "description",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
