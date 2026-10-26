from rest_framework import serializers
from .models import Post, PostImage, PostRating
from social.serializers import PublicUserSerializer
from django.db.models import Avg

class PostImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostImage
        fields = ("id", "image", "created_at")

class PostRatingSerializer(serializers.ModelSerializer):
    rater = PublicUserSerializer(read_only=True)

    class Meta:
        model = PostRating
        fields = (
            "id",
            "rater",
            "color_harmony",
            "trendy",
            "overall_matching",
            "accessories",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "rater", "created_at", "updated_at")

class PostSerializer(serializers.ModelSerializer):
    user = PublicUserSerializer(read_only=True)
    images = PostImageSerializer(many=True, read_only=True)
    uploaded_images = serializers.ListField(
        child=serializers.ImageField(allow_empty_file=False, use_url=False),
        write_only=True,
        required=False,
    )
    
    avg_color_harmony = serializers.SerializerMethodField()
    avg_trendy = serializers.SerializerMethodField()
    avg_overall_matching = serializers.SerializerMethodField()
    avg_accessories = serializers.SerializerMethodField()
    total_ratings = serializers.SerializerMethodField()
    user_rating = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = (
            "id",
            "user",
            "caption",
            "privacy",
            "images",
            "uploaded_images",
            "avg_color_harmony",
            "avg_trendy",
            "avg_overall_matching",
            "avg_accessories",
            "total_ratings",
            "user_rating",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def _get_avg(self, obj, field_name):
        if not hasattr(obj, "_prefetched_ratings_avg"):
            return None
        return obj._prefetched_ratings_avg.get(field_name)

    def get_avg_color_harmony(self, obj) -> float | None:
        return self._get_avg(obj, "color_harmony__avg")

    def get_avg_trendy(self, obj) -> float | None:
        return self._get_avg(obj, "trendy__avg")

    def get_avg_overall_matching(self, obj) -> float | None:
        return self._get_avg(obj, "overall_matching__avg")

    def get_avg_accessories(self, obj) -> float | None:
        return self._get_avg(obj, "accessories__avg")

    def get_total_ratings(self, obj) -> int:
        if not hasattr(obj, "_prefetched_ratings_count"):
            return 0
        return obj._prefetched_ratings_count

    def get_user_rating(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        
        if hasattr(obj, "_user_rating_prefetched"):
            rating = obj._user_rating_prefetched
            if rating:
                return PostRatingSerializer(rating).data
            return None
            
        rating = PostRating.objects.filter(post=obj, rater=request.user).first()
        if rating:
            return PostRatingSerializer(rating).data
        return None

    def create(self, validated_data):
        uploaded_images = validated_data.pop("uploaded_images", [])
        post = Post.objects.create(**validated_data)
        
        for image in uploaded_images:
            PostImage.objects.create(post=post, image=image)
            
        return post


class FriendsStyleRatingSerializer(serializers.Serializer):
    total_ratings = serializers.IntegerField(default=0)
    average_rating = serializers.FloatField(allow_null=True)
    color_harmony = serializers.FloatField(allow_null=True)
    trendy = serializers.FloatField(allow_null=True)
    overall_matching = serializers.FloatField(allow_null=True)
    accessories = serializers.FloatField(allow_null=True)
    user_rating = serializers.DictField(allow_null=True, required=False)


class FriendsStyleReasoningSerializer(serializers.Serializer):
    title = serializers.CharField(allow_blank=True, allow_null=True)
    subtitle = serializers.CharField(allow_blank=True, allow_null=True)
    items = serializers.ListField(child=serializers.CharField(), default=list)
    note = serializers.CharField(allow_blank=True, allow_null=True)


class FriendsStyleItemSerializer(serializers.Serializer):
    style_type = serializers.ChoiceField(
        choices=["Outfit", "Post"],
        help_text="Discriminator: 'Outfit' for friend's daily public outfit, 'Post' for feed post.",
    )
    id = serializers.IntegerField(help_text="ID of the SavedOutfit (if Outfit) or Post (if Post)")
    outfit_id = serializers.IntegerField(allow_null=True, required=False, help_text="OutfitJob ID")
    post_id = serializers.IntegerField(allow_null=True, required=False, help_text="Post ID")

    friend_id = serializers.IntegerField(help_text="User ID of the friend")
    friend_name = serializers.CharField(help_text="Full display name of the friend")
    friend_username = serializers.CharField(help_text="Username of the friend")
    friend_avatar = serializers.CharField(allow_null=True, help_text="Avatar/profile picture URL of the friend")

    outfit_image = serializers.CharField(allow_null=True, help_text="Main image URL (outfit try-on image or primary post image)")
    images = serializers.ListField(child=serializers.CharField(), default=list, help_text="List of image URLs (for post carousel/multi-images)")
    caption = serializers.CharField(allow_blank=True, allow_null=True, help_text="Post caption (null for Outfit)")

    date = serializers.DateField(help_text="Target calendar date (YYYY-MM-DD)")
    created_at = serializers.DateTimeField(help_text="Creation timestamp")

    is_saved_by_viewer = serializers.BooleanField(allow_null=True, required=False, help_text="Whether authenticated user has saved this outfit")
    is_shared = serializers.BooleanField(required=False, default=True, help_text="Public visibility flag")
    is_daily_selection = serializers.BooleanField(required=False, default=False, help_text="Whether this is the user's selected daily outfit")
    privacy = serializers.CharField(required=False, default="public")

    reasoning = FriendsStyleReasoningSerializer(allow_null=True, required=False, help_text="Outfit styling breakdown (null for Post)")
    ratings = FriendsStyleRatingSerializer(allow_null=True, required=False, help_text="Rating averages and total count")


class SaveFriendOutfitSerializer(serializers.Serializer):
    outfit_id = serializers.IntegerField(
        required=False,
        help_text="ID of the friend's OutfitJob to save. Either outfit_id or saved_outfit_id must be provided."
    )
    saved_outfit_id = serializers.IntegerField(
        required=False,
        help_text="ID of the friend's SavedOutfit. Either outfit_id or saved_outfit_id must be provided."
    )
    date = serializers.DateField(
        required=False,
        help_text="Calendar date to associate with your saved outfit (defaults to today)."
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional personal note for this saved outfit."
    )
    action = serializers.ChoiceField(
        choices=["save", "unsave", "toggle"],
        default="save",
        required=False,
        help_text="Action to perform: 'save' (default), 'unsave', or 'toggle'."
    )

    def validate(self, attrs):
        if not attrs.get("outfit_id") and not attrs.get("saved_outfit_id"):
            raise serializers.ValidationError("Either outfit_id or saved_outfit_id must be provided.")
        return attrs


class SaveFriendOutfitResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    is_saved = serializers.BooleanField()
    outfit_id = serializers.IntegerField()
    saved_outfit_id = serializers.IntegerField(allow_null=True)
    saved_date = serializers.DateField(allow_null=True)

