from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from avatars.models import Avatar
from wardrobe_items_ai.models import ItemAnalysis, JobStatus as ItemJobStatus, WardrobeItem
from .models import JobStatus, OutfitJob, OutfitRating, SavedOutfit

User = get_user_model()


class TryOnCreateSerializer(serializers.Serializer):
    avatar_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="ID of the Avatar belonging to the user. Optional: defaults to user's preferred avatar.",
    )
    wardrobe_item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text="List of WardrobeItem IDs to try on (max 1 item per category).",
    )

    def validate_avatar_id(self, value):
        if not value:
            return None
        user = self.context["request"].user
        try:
            avatar = Avatar.objects.get(pk=value)
        except Avatar.DoesNotExist:
            raise serializers.ValidationError(f"Avatar with ID {value} does not exist.")

        if not avatar.is_default and avatar.user != user:
            raise serializers.ValidationError("Avatar does not belong to the authenticated user.")

        if avatar.status != Avatar.JobStatus.DONE and not avatar.result_image and not avatar.fal_cdn_url:
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
        attrs = super().validate(attrs)
        if not attrs.get("avatar_id"):
            user = self.context["request"].user
            from outfits.services import resolve_tryon_avatar
            attrs["avatar"] = resolve_tryon_avatar(user)
        else:
            attrs["avatar"] = attrs.get("avatar_id")
        attrs["wardrobe_items"] = attrs.get("wardrobe_item_ids")
        return attrs


class OutfitJobSerializer(serializers.ModelSerializer):
    avatar = serializers.PrimaryKeyRelatedField(read_only=True)
    wardrobe_items = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    result_image = serializers.SerializerMethodField()
    saved = serializers.BooleanField(source="is_saved", read_only=True)
    generated_date = serializers.DateField(source="scheduled_date", read_only=True)

    class Meta:
        model = OutfitJob
        fields = [
            "id",
            "generated_date",
            "trigger_type",
            "status",
            "avatar",
            "wardrobe_items",
            "result_image",
            "is_saved",
            "saved",
            "reasoning_title",
            "reasoning_subtitle",
            "reasoning_items",
            "reasoning_note",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_result_image(self, obj):
        url = obj.display_result_image
        return url if url else None


class OutfitJobStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    result_image = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(required=False, allow_blank=True)


class TodayOutfitSerializer(serializers.ModelSerializer):
    date = serializers.DateField(source="scheduled_date", read_only=True)
    avatar = serializers.PrimaryKeyRelatedField(read_only=True)
    wardrobe_items = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    result_image = serializers.SerializerMethodField()
    saved = serializers.BooleanField(source="is_saved", read_only=True)

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
            "is_saved",
            "saved",
            "reasoning_title",
            "reasoning_subtitle",
            "reasoning_items",
            "reasoning_note",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_result_image(self, obj):
        if obj.status == JobStatus.DONE:
            url = obj.display_result_image
            return url if url else None
        return None


class SavedOutfitCreateSerializer(serializers.ModelSerializer):
    saved_date = serializers.DateField(
        source="date",
        help_text="Date for which the outfit is saved.",
    )
    outfit_job_id = serializers.IntegerField(
        help_text="ID of a completed OutfitJob belonging to the user."
    )

    class Meta:
        model = SavedOutfit
        fields = ["outfit_job_id", "saved_date", "note", "is_shared"]
        extra_kwargs = {"is_shared": {"required": False}}

    def to_internal_value(self, data):
        if isinstance(data, dict) and "date" in data and "saved_date" not in data:
            data = data.copy()
            data["saved_date"] = data.pop("date")
        return super().to_internal_value(data)

    def validate_outfit_job_id(self, value):
        user = self.context["request"].user
        try:
            job = OutfitJob.objects.get(pk=value)
        except OutfitJob.DoesNotExist:
            raise serializers.ValidationError(f"OutfitJob with ID {value} does not exist.")

        if job.user != user:
            raise serializers.ValidationError("OutfitJob does not belong to the authenticated user.")

        if job.status != JobStatus.DONE:
            raise serializers.ValidationError(
                f"OutfitJob must be completed before saving. Current status: {job.status}."
            )

        return job

    def validate(self, attrs):
        attrs["outfit_job"] = attrs.pop("outfit_job_id")
        return attrs

    def create(self, validated_data):
        from .services import save_try_on_to_cloudinary

        job = validated_data.pop("outfit_job")
        saved_date = validated_data.pop("date")

        save_try_on_to_cloudinary(job)

        user = validated_data.pop("user", None) or self.context["request"].user

        saved_outfit, _ = SavedOutfit.objects.update_or_create(
            user=user,
            date=saved_date,
            defaults={
                "outfit_job": job,
                **validated_data,
            },
        )
        return saved_outfit


class SavedOutfitUpdateSerializer(serializers.ModelSerializer):
    saved_date = serializers.DateField(
        source="date",
        required=False,
        help_text="Date for which the outfit is saved.",
    )
    outfit_job_id = serializers.IntegerField(
        required=False,
        help_text="ID of a completed OutfitJob belonging to the user.",
    )

    class Meta:
        model = SavedOutfit
        fields = ["saved_date", "outfit_job_id", "note", "is_shared"]

    def to_internal_value(self, data):
        if isinstance(data, dict) and "date" in data and "saved_date" not in data:
            data = data.copy()
            data["saved_date"] = data.pop("date")
        return super().to_internal_value(data)

    def validate_outfit_job_id(self, value):
        user = self.context["request"].user
        try:
            job = OutfitJob.objects.get(pk=value)
        except OutfitJob.DoesNotExist:
            raise serializers.ValidationError(f"OutfitJob with ID {value} does not exist.")

        if job.user != user:
            raise serializers.ValidationError("OutfitJob does not belong to the authenticated user.")

        if job.status != JobStatus.DONE:
            raise serializers.ValidationError(
                f"OutfitJob must be completed before saving. Current status: {job.status}."
            )

        return job

    def validate(self, attrs):
        if "outfit_job_id" in attrs:
            attrs["outfit_job"] = attrs.pop("outfit_job_id")
        return attrs

    def update(self, instance, validated_data):
        from .services import save_try_on_to_cloudinary
        if "outfit_job" in validated_data:
            save_try_on_to_cloudinary(validated_data["outfit_job"])
        return super().update(instance, validated_data)


class SavedOutfitSerializer(serializers.ModelSerializer):
    saved_date = serializers.DateField(source="date", read_only=True)
    outfit_job = OutfitJobSerializer(read_only=True)

    class Meta:
        model = SavedOutfit
        fields = [
            "id",
            "saved_date",
            "note",
            "is_shared",
            "outfit_job",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


RATING_CATEGORIES = ["color_harmony", "trendy", "overall_matching", "accessories"]


class OutfitRatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutfitRating
        fields = RATING_CATEGORIES


class PublicSavedOutfitSerializer(serializers.ModelSerializer):
    saved_date = serializers.DateField(source="date", read_only=True)
    outfit_job = OutfitJobSerializer(read_only=True)
    ratings_count = serializers.IntegerField(read_only=True, default=0)
    average_rating = serializers.SerializerMethodField()
    rating_breakdown = serializers.SerializerMethodField()

    class Meta:
        model = SavedOutfit
        fields = [
            "id",
            "saved_date",
            "note",
            "outfit_job",
            "ratings_count",
            "average_rating",
            "rating_breakdown",
            "created_at",
        ]
        read_only_fields = fields

    def _category_averages(self, obj):
        return {field: getattr(obj, f"avg_{field}", None) for field in RATING_CATEGORIES}

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_average_rating(self, obj):
        values = [v for v in self._category_averages(obj).values() if v is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @extend_schema_field(serializers.DictField(child=serializers.FloatField(allow_null=True)))
    def get_rating_breakdown(self, obj):
        return {
            field: (round(value, 1) if value is not None else None)
            for field, value in self._category_averages(obj).items()
        }


class RaterSerializer(serializers.ModelSerializer):
    """Small user card identifying who submitted a rating."""

    class Meta:
        model = User
        fields = ["id", "username", "name", "profile_image"]
        read_only_fields = fields


class SavedOutfitRatingDetailSerializer(serializers.ModelSerializer):
    """
    One individual rating with who gave it — only ever shown to the outfit's
    owner (see SavedOutfitRatingsListView), never on the public profile.
    """

    rater = RaterSerializer(read_only=True)

    class Meta:
        model = OutfitRating
        fields = ["id", "rater", *RATING_CATEGORIES, "created_at"]
        read_only_fields = fields

