from datetime import date as date_type, timedelta
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, OuterRef, Prefetch, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from core.pagination import StandardPagination
from outfits.models import DailyOutfitSelection, JobStatus, OutfitJob, OutfitRating, SavedOutfit
from outfits.views import with_rating_aggregates
from social.models import Follow
from .models import Post, PostImage, PostRating
from .serializers import (
    FriendsStyleItemSerializer,
    PostRatingSerializer,
    PostSerializer,
    SaveFriendOutfitResponseSerializer,
    SaveFriendOutfitSerializer,
)

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


class FriendsStyleFeedView(APIView):
    """
    GET /api/v1/feed/friends-style/
    Retrieve public outfits and posts of connected friends for a specific calendar day.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    @extend_schema(
        parameters=[],
        responses={200: FriendsStyleItemSerializer(many=True)},
        summary="Friends Style Feed",
        description="Returns today's public outfits and posts of connected friends in a single unified feed.",
    )
    def get(self, request, *args, **kwargs):
        # Default to today
        target_date = timezone.now().date()
        date_param = request.query_params.get("date")
        if date_param:
            if date_param.strip().lower() == "yesterday":
                target_date = timezone.now().date() - timedelta(days=1)
            else:
                try:
                    target_date = date_type.fromisoformat(date_param.strip())
                except (ValueError, TypeError):
                    pass

        style_type = request.query_params.get("style_type", "all").strip().lower()

        # 3. Get connected friends
        friend_ids = list(
            Follow.objects.filter(follower=request.user).values_list("following_id", flat=True)
        )
        if not friend_ids:
            paginator = self.pagination_class()
            page = paginator.paginate_queryset([], request)
            return paginator.get_paginated_response(page)

        combined_items = []

        # 4. Fetch Outfits
        if style_type in ("all", "outfit"):
            daily_selections = DailyOutfitSelection.objects.filter(
                user_id__in=friend_ids,
                date=target_date,
            ).select_related("outfit_job")
            daily_job_ids = {ds.outfit_job_id for ds in daily_selections if ds.outfit_job_id}

            outfit_date_q = (
                Q(date=target_date)
                | Q(created_at__date=target_date)
                | Q(updated_at__date=target_date)
                | Q(outfit_job__scheduled_date=target_date)
                | Q(outfit_job__created_at__date=target_date)
            )
            if daily_job_ids:
                outfit_date_q |= Q(outfit_job_id__in=daily_job_ids)

            saved_outfits_qs = with_rating_aggregates(
                SavedOutfit.objects.filter(
                    user_id__in=friend_ids,
                    is_shared=True,
                )
                .filter(outfit_date_q)
                .select_related("user", "outfit_job", "outfit_job__avatar")
                .distinct()
            )
            all_outfits = list(saved_outfits_qs)

            # Check viewer's saved jobs
            job_ids = [o.outfit_job_id for o in all_outfits if o.outfit_job_id]
            viewer_saved_jobs = set(
                SavedOutfit.objects.filter(
                    user=request.user,
                    outfit_job_id__in=job_ids,
                ).values_list("outfit_job_id", flat=True)
            )

            # Check viewer's ratings
            saved_outfit_ids = [o.id for o in all_outfits]
            viewer_outfit_ratings = {
                r.saved_outfit_id: r
                for r in OutfitRating.objects.filter(
                    saved_outfit_id__in=saved_outfit_ids,
                    rater=request.user,
                )
            }

            for outfit in all_outfits:
                friend = outfit.user
                friend_name = friend.name or friend.username
                friend_avatar = request.build_absolute_uri(friend.profile_image.url) if friend.profile_image else None
                job = outfit.outfit_job

                outfit_img_url = job.display_result_image if job else ""
                if outfit_img_url and outfit_img_url.startswith("/"):
                    outfit_img_url = request.build_absolute_uri(outfit_img_url)

                avg_vals = [
                    getattr(outfit, "avg_color_harmony", None),
                    getattr(outfit, "avg_trendy", None),
                    getattr(outfit, "avg_overall_matching", None),
                    getattr(outfit, "avg_accessories", None),
                ]
                numeric_avgs = [v for v in avg_vals if v is not None]
                average_rating = round(sum(numeric_avgs) / len(numeric_avgs), 1) if numeric_avgs else None

                viewer_r = viewer_outfit_ratings.get(outfit.id)
                user_rating_dict = None
                if viewer_r:
                    user_rating_dict = {
                        "color_harmony": viewer_r.color_harmony,
                        "trendy": viewer_r.trendy,
                        "overall_matching": viewer_r.overall_matching,
                        "accessories": viewer_r.accessories,
                    }

                ratings_dict = {
                    "total_ratings": getattr(outfit, "ratings_count", 0),
                    "average_rating": average_rating,
                    "color_harmony": round(getattr(outfit, "avg_color_harmony"), 1) if getattr(outfit, "avg_color_harmony") is not None else None,
                    "trendy": round(getattr(outfit, "avg_trendy"), 1) if getattr(outfit, "avg_trendy") is not None else None,
                    "overall_matching": round(getattr(outfit, "avg_overall_matching"), 1) if getattr(outfit, "avg_overall_matching") is not None else None,
                    "accessories": round(getattr(outfit, "avg_accessories"), 1) if getattr(outfit, "avg_accessories") is not None else None,
                    "user_rating": user_rating_dict,
                }

                reasoning_dict = {
                    "title": job.reasoning_title or "",
                    "subtitle": job.reasoning_subtitle or "",
                    "items": job.reasoning_items or [],
                    "note": job.reasoning_note or "",
                } if job else None

                combined_items.append({
                    "_sort_time": outfit.updated_at or outfit.created_at,
                    "style_type": "Outfit",
                    "id": outfit.id,
                    "outfit_id": job.id if job else None,
                    "post_id": None,
                    "friend_id": friend.id,
                    "friend_name": friend_name,
                    "friend_username": friend.username,
                    "friend_avatar": friend_avatar,
                    "outfit_image": outfit_img_url,
                    "images": [outfit_img_url] if outfit_img_url else [],
                    "caption": None,
                    "date": target_date,
                    "created_at": outfit.created_at,
                    "is_saved_by_viewer": (job.id in viewer_saved_jobs) if job else False,
                    "is_shared": True,
                    "is_daily_selection": (job.id in daily_job_ids) if job else False,
                    "privacy": "public",
                    "reasoning": reasoning_dict,
                    "ratings": ratings_dict,
                })

        # 5. Fetch Posts
        if style_type in ("all", "post"):
            posts_qs = (
                Post.objects.filter(
                    user_id__in=friend_ids,
                    privacy="public",
                    created_at__date=target_date,
                )
                .annotate(
                    _ratings_count=Count("ratings", distinct=True),
                    color_harmony__avg=Avg("ratings__color_harmony"),
                    trendy__avg=Avg("ratings__trendy"),
                    overall_matching__avg=Avg("ratings__overall_matching"),
                    accessories__avg=Avg("ratings__accessories"),
                )
                .select_related("user")
                .prefetch_related("images")
            )
            posts_list = list(posts_qs)
            post_ids = [p.id for p in posts_list]
            viewer_post_ratings = {
                r.post_id: r
                for r in PostRating.objects.filter(
                    post_id__in=post_ids,
                    rater=request.user,
                )
            }

            for post in posts_list:
                friend = post.user
                friend_name = friend.name or friend.username
                friend_avatar = request.build_absolute_uri(friend.profile_image.url) if friend.profile_image else None

                post_images = []
                first_image_url = None
                for img in post.images.all():
                    url = request.build_absolute_uri(img.image.url) if img.image else None
                    if url:
                        if not first_image_url:
                            first_image_url = url
                        post_images.append(url)

                avg_vals = [
                    post.color_harmony__avg,
                    post.trendy__avg,
                    post.overall_matching__avg,
                    post.accessories__avg,
                ]
                numeric_avgs = [v for v in avg_vals if v is not None]
                average_rating = round(sum(numeric_avgs) / len(numeric_avgs), 1) if numeric_avgs else None

                viewer_r = viewer_post_ratings.get(post.id)
                user_rating_dict = None
                if viewer_r:
                    user_rating_dict = {
                        "color_harmony": viewer_r.color_harmony,
                        "trendy": viewer_r.trendy,
                        "overall_matching": viewer_r.overall_matching,
                        "accessories": viewer_r.accessories,
                    }

                ratings_dict = {
                    "total_ratings": getattr(post, "_ratings_count", 0),
                    "average_rating": average_rating,
                    "color_harmony": round(post.color_harmony__avg, 1) if post.color_harmony__avg is not None else None,
                    "trendy": round(post.trendy__avg, 1) if post.trendy__avg is not None else None,
                    "overall_matching": round(post.overall_matching__avg, 1) if post.overall_matching__avg is not None else None,
                    "accessories": round(post.accessories__avg, 1) if post.accessories__avg is not None else None,
                    "user_rating": user_rating_dict,
                }

                combined_items.append({
                    "_sort_time": post.created_at,
                    "style_type": "Post",
                    "id": post.id,
                    "outfit_id": None,
                    "post_id": post.id,
                    "friend_id": friend.id,
                    "friend_name": friend_name,
                    "friend_username": friend.username,
                    "friend_avatar": friend_avatar,
                    "outfit_image": first_image_url,
                    "images": post_images,
                    "caption": post.caption or "",
                    "date": target_date,
                    "created_at": post.created_at,
                    "is_saved_by_viewer": None,
                    "is_shared": True,
                    "is_daily_selection": False,
                    "privacy": post.privacy,
                    "reasoning": None,
                    "ratings": ratings_dict,
                })

        # 6. Sort combined results by activity timestamp descending
        combined_items.sort(key=lambda x: x["_sort_time"], reverse=True)
        for item in combined_items:
            item.pop("_sort_time", None)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(combined_items, request)
        if page is not None:
            serializer = FriendsStyleItemSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = FriendsStyleItemSerializer(combined_items, many=True)
        return Response(serializer.data)


class SaveFriendOutfitView(APIView):
    """
    POST /api/v1/feed/friends-style/save-outfit/
    POST /api/v1/feed/friends-style/outfits/<int:pk>/save/
    DELETE /api/v1/feed/friends-style/outfits/<int:pk>/save/

    Saves a friend's public outfit to the authenticated user's own SavedOutfit collection,
    or unsaves / toggles it.
    """
    permission_classes = [IsAuthenticated]

    def _resolve_job(self, request, pk=None, data=None):
        outfit_id = pk or (data.get("outfit_id") if data else None)
        saved_outfit_id = data.get("saved_outfit_id") if data else None

        job = None
        if outfit_id:
            job = OutfitJob.objects.filter(pk=outfit_id).first()
            if not job:
                # If pk/outfit_id was actually a SavedOutfit ID
                saved_entry = SavedOutfit.objects.filter(pk=outfit_id).select_related("outfit_job").first()
                if saved_entry:
                    job = saved_entry.outfit_job

        if not job and saved_outfit_id:
            saved_entry = SavedOutfit.objects.filter(pk=saved_outfit_id).select_related("outfit_job").first()
            if saved_entry:
                job = saved_entry.outfit_job

        if not job:
            raise Http404("Outfit not found.")

        # Permission check: must be public (is_shared=True on any SavedOutfit) or the user's own
        is_public = SavedOutfit.objects.filter(outfit_job=job, is_shared=True).exists()
        if not is_public and job.user != request.user:
            raise PermissionDenied("This outfit is private and cannot be saved.")

        if job.status != JobStatus.DONE:
            raise ValidationError({"detail": f"OutfitJob must be completed before saving. Current status: {job.status}."})

        return job

    @extend_schema(
        request=None,
        responses={
            200: SaveFriendOutfitResponseSerializer,
            201: SaveFriendOutfitResponseSerializer,
        },
        summary="Save friend's outfit",
        description="Saves a friend's public outfit to the user's saved collection (POST).",
    )
    def post(self, request, pk=None):
        data = request.data if isinstance(request.data, dict) else {}
        action = data.get("action", "save").lower()

        job = self._resolve_job(request, pk=pk, data=data)

        # Handle 'unsave' action
        if action == "unsave":
            SavedOutfit.objects.filter(user=request.user, outfit_job=job).delete()
            return Response(
                {
                    "status": "success",
                    "message": "Outfit removed from your saved collection.",
                    "is_saved": False,
                    "outfit_id": job.id,
                    "saved_outfit_id": None,
                    "saved_date": None,
                },
                status=status.HTTP_200_OK,
            )

        # Handle 'toggle' action
        if action == "toggle":
            existing = SavedOutfit.objects.filter(user=request.user, outfit_job=job).first()
            if existing:
                existing.delete()
                return Response(
                    {
                        "status": "success",
                        "message": "Outfit removed from your saved collection.",
                        "is_saved": False,
                        "outfit_id": job.id,
                        "saved_outfit_id": None,
                        "saved_date": None,
                    },
                    status=status.HTTP_200_OK,
                )

        # Default: Save
        target_date_val = data.get("date")
        if target_date_val:
            try:
                target_date = date_type.fromisoformat(str(target_date_val).strip())
            except (ValueError, TypeError):
                raise ValidationError({"date": ["Invalid date format. Use YYYY-MM-DD."]})
        else:
            target_date = timezone.now().date()

        note_val = data.get("note", "").strip() or f"Saved from @{job.user.username}"

        saved_outfit, created = SavedOutfit.objects.get_or_create(
            user=request.user,
            outfit_job=job,
            defaults={
                "date": target_date,
                "note": note_val,
                "is_shared": False,
            },
        )

        res_data = {
            "status": "success",
            "message": "Friend's outfit saved successfully." if created else "Outfit is already saved in your collection.",
            "is_saved": True,
            "outfit_id": job.id,
            "saved_outfit_id": saved_outfit.id,
            "saved_date": saved_outfit.date,
        }
        res_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(res_data, status=res_status)

    @extend_schema(
        responses={200: SaveFriendOutfitResponseSerializer},
        summary="Unsave friend outfit",
        description="Removes a saved friend outfit from the user's saved collection.",
    )
    def delete(self, request, pk=None):
        data = request.data if isinstance(request.data, dict) else {}
        job = self._resolve_job(request, pk=pk, data=data)

        SavedOutfit.objects.filter(user=request.user, outfit_job=job).delete()
        return Response(
            {
                "status": "success",
                "message": "Outfit removed from your saved collection.",
                "is_saved": False,
                "outfit_id": job.id,
                "saved_outfit_id": None,
                "saved_date": None,
            },
            status=status.HTTP_200_OK,
        )

