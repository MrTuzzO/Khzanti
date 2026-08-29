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
