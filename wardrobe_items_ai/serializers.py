from rest_framework import serializers
from wardrobe.models import Category
from wardrobe.serializers import CategorySerializer
from .models import ItemAnalysis, WardrobeItem


class CategorySlugOrPKRelatedField(serializers.RelatedField):
    def get_queryset(self):
        return Category.objects.all()

    def to_internal_value(self, data):
        if isinstance(data, Category):
            return data

        val_str = str(data).strip()
        if val_str.isdigit():
            try:
                return Category.objects.get(pk=int(val_str))
            except Category.DoesNotExist:
                raise serializers.ValidationError(f"Category with ID {val_str} does not exist.")

        try:
            return Category.objects.get(slug__iexact=val_str)
        except Category.DoesNotExist:
            pass

        try:
            return Category.objects.get(name__iexact=val_str)
        except Category.DoesNotExist:
            raise serializers.ValidationError(f"Category '{val_str}' does not exist.")

    def to_representation(self, value):
        return value.pk


class WardrobeItemCreateSerializer(serializers.ModelSerializer):
    category = CategorySlugOrPKRelatedField(help_text="Category ID (integer) or category name/slug (e.g. 'shoes', 'tops').")

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


class ItemAnalysisTriggerSerializer(serializers.Serializer):
    wardrobe_item_id = serializers.IntegerField(
        required=False,
        help_text="ID of the wardrobe item to analyze.",
    )
    wardrobe_item = serializers.IntegerField(
        required=False,
        help_text="Alias for wardrobe_item_id.",
    )

    def validate(self, attrs):
        item_id = attrs.get("wardrobe_item_id") or attrs.get("wardrobe_item")
        if item_id is None:
            raise serializers.ValidationError({"wardrobe_item_id": ["This field is required."]})
        attrs["wardrobe_item_id"] = item_id
        return attrs