from django.db.models import Avg, Count, OuterRef, Subquery, Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from .models import Post, PostImage, PostRating
from .serializers import PostSerializer, PostRatingSerializer
from core.pagination import StandardPagination
from social.models import Follow

class PostViewSet(viewsets.ModelViewSet):
    serializer_class = PostSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        user = self.request.user
        qs = Post.objects.all()

        # Check if filtering by username (for profile view)
        target_username = self.request.query_params.get("username")
        
        if target_username:
            if target_username == user.username:
                # Viewing own profile: see all own posts
                qs = qs.filter(user=user)
            else:
                # Viewing someone else's profile: see only their public posts
                qs = qs.filter(user__username=target_username, privacy="public")
        else:
            # Default behavior if no username provided: just show own posts
            qs = qs.filter(user=user)
        
        # Subquery for user's own rating
        user_rating_qs = PostRating.objects.filter(post=OuterRef("pk"), rater=user)
        
        qs = qs.annotate(
            _prefetched_ratings_count=Count("ratings", distinct=True),
        )
        
        # We also need averages. Django's annotate with Avg can get messy with multiple counts/joins.
        # Let's aggregate averages using a separate query or standard aggregation.
        qs = qs.annotate(
            color_harmony__avg=Avg("ratings__color_harmony"),
            trendy__avg=Avg("ratings__trendy"),
            overall_matching__avg=Avg("ratings__overall_matching"),
            accessories__avg=Avg("ratings__accessories"),
        )
        
        qs = qs.prefetch_related(
            "images",
            "user",
            Prefetch(
                "ratings",
                queryset=PostRating.objects.filter(rater=user),
                to_attr="_user_rating_list"
            )
        )
        
        return qs.order_by("-created_at")

    def get_object(self):
        obj = super().get_object()
        if hasattr(obj, "_user_rating_list") and obj._user_rating_list:
            obj._user_rating_prefetched = obj._user_rating_list[0]
        else:
            obj._user_rating_prefetched = None
            
        # Put averages in a dict for serializer
        obj._prefetched_ratings_avg = {
            "color_harmony__avg": obj.color_harmony__avg,
            "trendy__avg": obj.trendy__avg,
            "overall_matching__avg": obj.overall_matching__avg,
            "accessories__avg": obj.accessories__avg,
        }
        return obj

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="username",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter posts by a specific user's username for profile views. If omitted, returns the authenticated user's posts.",
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            for obj in page:
                if hasattr(obj, "_user_rating_list") and obj._user_rating_list:
                    obj._user_rating_prefetched = obj._user_rating_list[0]
                else:
                    obj._user_rating_prefetched = None
                    
                obj._prefetched_ratings_avg = {
                    "color_harmony__avg": obj.color_harmony__avg,
                    "trendy__avg": obj.trendy__avg,
                    "overall_matching__avg": obj.overall_matching__avg,
                    "accessories__avg": obj.accessories__avg,
                }
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        for obj in queryset:
            if hasattr(obj, "_user_rating_list") and obj._user_rating_list:
                obj._user_rating_prefetched = obj._user_rating_list[0]
            else:
                obj._user_rating_prefetched = None
                
            obj._prefetched_ratings_avg = {
                "color_harmony__avg": obj.color_harmony__avg,
                "trendy__avg": obj.trendy__avg,
                "overall_matching__avg": obj.overall_matching__avg,
                "accessories__avg": obj.accessories__avg,
            }
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        if self.get_object().user != self.request.user:
            raise ValidationError({"detail": "You cannot edit someone else's post."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.user != self.request.user:
            raise ValidationError({"detail": "You cannot delete someone else's post."})
        instance.delete()


class RatePostView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=PostRatingSerializer,
        responses={200: PostRatingSerializer, 201: PostRatingSerializer}
    )
    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        
        if post.privacy != "public" and post.user != request.user:
            raise ValidationError({"detail": "This post is private."})

        rating, created = PostRating.objects.get_or_create(
            post=post,
            rater=request.user,
            defaults={
                "color_harmony": request.data.get("color_harmony", 0),
                "trendy": request.data.get("trendy", 0),
                "overall_matching": request.data.get("overall_matching", 0),
                "accessories": request.data.get("accessories", 0),
            }
        )
        
        if not created:
            rating.color_harmony = request.data.get("color_harmony", rating.color_harmony)
            rating.trendy = request.data.get("trendy", rating.trendy)
            rating.overall_matching = request.data.get("overall_matching", rating.overall_matching)
            rating.accessories = request.data.get("accessories", rating.accessories)
            rating.save()
            status_code = status.HTTP_200_OK
        else:
            status_code = status.HTTP_201_CREATED

        serializer = PostRatingSerializer(rating)
        return Response(serializer.data, status=status_code)
