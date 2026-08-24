from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework import serializers
from wardrobe.models import Category
from wardrobe.serializers import CategorySerializer
from .models import ItemAnalysis, WardrobeItem





class WardrobeItemCreateSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        help_text="Category ID (integer)."
    )

    class Meta:
        model = WardrobeItem
        fields = [
            "id",
            "image",
            "category",
            "season",
            "occasion",
            "purchase_source",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]



class ItemAnalysisSerializer(serializers.ModelSerializer):
    display_url = serializers.ReadOnlyField()
    processed_image = serializers.SerializerMethodField()

    class Meta:
        model = ItemAnalysis
        fields = [
            "id",
            "wardrobe_item",
            "status",
            "is_saved",
            "display_url",
            "fal_cdn_url",
            "processed_image",
            "color",
            "description",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_processed_image(self, obj):
        if obj.is_saved and obj.processed_image:
            try:
                return obj.processed_image.url
            except Exception:
                pass
        return None



class WardrobeItemSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    analysis = ItemAnalysisSerializer(read_only=True)

    class Meta:
        model = WardrobeItem
        fields = [
            "id",
            "image",
            "category",
            "season",
            "occasion",
            "purchase_source",
            "analysis",
            "created_at",
        ]
        read_only_fields = fields