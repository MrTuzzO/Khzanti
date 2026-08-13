from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from core.pagination import StandardPagination
from .models import Follow
from .serializers import PublicUserSerializer


def annotated_users(viewer):
    """Base queryset for User cards: adds follow counts + whether `viewer` follows them."""
    return User.objects.filter(is_active=True).annotate(
        followers_count=Count("followers", distinct=True),
        following_count=Count("following", distinct=True),
        is_following=Exists(Follow.objects.filter(follower_id=viewer.id, following_id=OuterRef("pk"))),
    )


class UserSearchView(generics.ListAPIView):
    """Search users by username or name, e.g. GET /social/search/?q=tuzzo"""

    permission_classes = [IsAuthenticated]
    serializer_class = PublicUserSerializer
    pagination_class = StandardPagination

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="q",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Search term matched against username and name.",
            ),
        ],
        responses={200: PublicUserSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        if not query:
            return User.objects.none()

        return (
            annotated_users(self.request.user)
            .filter(Q(username__icontains=query) | Q(name__icontains=query))
            .exclude(pk=self.request.user.pk)
            .order_by("username")
        )


class PublicProfileView(generics.RetrieveAPIView):
    """GET /social/users/<username>/ - public profile with follow counts."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublicUserSerializer
    lookup_field = "username"

    def get_queryset(self):
        return annotated_users(self.request.user)


class FollowActionView(APIView):
    """POST /social/users/<username>/follow/ to follow, DELETE to unfollow."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        target = get_object_or_404(User, username=username, is_active=True)
        if target == request.user:
            raise ValidationError({"detail": "You cannot follow yourself."})

        _, created = Follow.objects.get_or_create(follower=request.user, following=target)
        return Response(
            {"detail": "Now following." if created else "Already following."},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, username):
        target = get_object_or_404(User, username=username)
        Follow.objects.filter(follower=request.user, following=target).delete()
        return Response({"detail": "Unfollowed."}, status=status.HTTP_200_OK)


class FollowersListView(generics.ListAPIView):
    """GET /social/users/<username>/followers/ - who follows this user."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublicUserSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        target = get_object_or_404(User, username=self.kwargs["username"])
        follower_ids = Follow.objects.filter(following=target).values("follower_id")
        return annotated_users(self.request.user).filter(pk__in=follower_ids).order_by("username")


class FollowingListView(generics.ListAPIView):
    """GET /social/users/<username>/following/ - who this user follows."""

    permission_classes = [IsAuthenticated]
    serializer_class = PublicUserSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        target = get_object_or_404(User, username=self.kwargs["username"])
        following_ids = Follow.objects.filter(follower=target).values("following_id")
        return annotated_users(self.request.user).filter(pk__in=following_ids).order_by("username")
