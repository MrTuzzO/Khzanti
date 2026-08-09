from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from avatars.models import Avatar
from wardrobe_items_ai.models import ItemAnalysis, JobStatus as ItemJobStatus, WardrobeItem
from .models import JobStatus, OutfitJob


class TryOnCreateSerializer(serializers.Serializer):
    avatar_id = serializers.IntegerField(
        help_text="ID of the Avatar belonging to the user."
    )
    wardrobe_item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text="List of WardrobeItem IDs to try on (max 1 item per category).",
    )

    def validate_avatar_id(self, value):
        user = self.context["request"].user
        try:
            avatar = Avatar.objects.get(pk=value)
        except Avatar.DoesNotExist:
            raise serializers.ValidationError(f"Avatar with ID {value} does not exist.")

        if avatar.user != user:
            raise serializers.ValidationError("Avatar does not belong to the authenticated user.")

        if avatar.status != Avatar.JobStatus.DONE and not avatar.result_image:
            raise serializers.ValidationError("Selected avatar generation is not completed yet.")

        return avatar

    def validate_wardrobe_item_ids(self, values):
        if not values:
            raise serializers.ValidationError("At least one wardrobe item must be selected.")

        user = self.context["request"].user
        items = list(
            WardrobeItem.objects.filter(pk__in=values)
            .select_related("category", "analysis")
        )

        found_ids = {item.id for item in items}
        for item_id in values:
            if item_id not in found_ids:
                raise serializers.ValidationError(f"Wardrobe item with ID {item_id} does not exist.")

        # Validate ownership
        for item in items:
            if item.user != user:
                raise serializers.ValidationError(
                    f"Wardrobe item #{item.id} does not belong to the authenticated user."
                )

        # Validate Category Uniqueness (Only one item per category)
        seen_categories = {}
        for item in items:
            cat_id = item.category_id
            cat_name = getattr(item.category, "name", f"ID {cat_id}")
            if cat_id in seen_categories:
                prev_item = seen_categories[cat_id]
                raise serializers.ValidationError(
                    f"Only one item per category is allowed. Multiple items selected for category '{cat_name}' (items #{prev_item.id} and #{item.id})."
                )
            seen_categories[cat_id] = item

        # Validate ItemAnalysis status and processed image
        for item in items:
            analysis = getattr(item, "analysis", None)
            if not analysis or analysis.status != ItemJobStatus.DONE:
                raise serializers.ValidationError(
                    f"Selected wardrobe item #{item.id} is still being processed. Please wait until processing is complete."
                )
            if not analysis.display_url and not analysis.processed_image:
                raise serializers.ValidationError(
                    f"Selected wardrobe item #{item.id} does not have a usable processed image."
                )

        return items

    def validate(self, attrs):
        attrs["avatar"] = attrs["avatar_id"]
        attrs["wardrobe_items"] = attrs["wardrobe_item_ids"]
        return attrs


class OutfitJobSerializer(serializers.ModelSerializer):
    avatar = serializers.PrimaryKeyRelatedField(read_only=True)
    wardrobe_items = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    result_image = serializers.SerializerMethodField()

    class Meta:
        model = OutfitJob
        fields = [
            "id",
            "status",
            "avatar",
            "wardrobe_items",
            "result_image",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_result_image(self, obj):
        if obj.result_image:
            try:
                return obj.result_image.url
            except Exception:
                pass
        return None


class OutfitJobStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    result_image = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(required=False, allow_blank=True)


class TodayOutfitSerializer(serializers.ModelSerializer):
    date = serializers.DateField(source="scheduled_date", read_only=True)
    avatar = serializers.PrimaryKeyRelatedField(read_only=True)
    wardrobe_items = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    result_image = serializers.SerializerMethodField()

    class Meta:
        model = OutfitJob
        fields = [
            "id",
            "date",
            "trigger_type",
            "status",
            "avatar",
            "wardrobe_items",
            "result_image",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_result_image(self, obj):
        if obj.status == JobStatus.DONE and obj.result_image:
            try:
                return obj.result_image.url
            except Exception:
                pass
        return None
