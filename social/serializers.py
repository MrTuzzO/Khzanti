from rest_framework import serializers

from accounts.models import User


class PublicUserSerializer(serializers.ModelSerializer):
    """User card for search results / followers / following lists / public profile.

    followers_count, following_count and is_following are expected to be annotated
    on the queryset by the view (see social/views.py) to avoid N+1 queries.
    """

    followers_count = serializers.IntegerField(read_only=True, default=0)
    following_count = serializers.IntegerField(read_only=True, default=0)
    shared_outfits_count = serializers.IntegerField(read_only=True, default=0)
    is_following = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "profile_image",
            "followers_count",
            "following_count",
            "shared_outfits_count",
            "is_following",
        )
        read_only_fields = fields
