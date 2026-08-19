import requests
from django.core.files.base import ContentFile
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ServiceError
from .models import Avatar
from .serializers import (
    AvatarCreateSerializer,
    AvatarSerializer,
    SystemDefaultAvatarAdminSerializer,
)
from .services import resolve_user_default_avatar, submit_avatar_job, sync_avatar_status


def _raise_if_avatar_failed(avatar: Avatar) -> None:
    if avatar.status == Avatar.JobStatus.FAILED:
        raise ServiceError(
            detail=avatar.error_message or "Avatar generation failed. Please try again later.",
            debug_detail=avatar.internal_error_detail,
            status_code=502,
        )


class AvatarCreateView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarCreateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        avatar = serializer.save(user=request.user, is_default=False, status=Avatar.JobStatus.PENDING)
        submit_avatar_job(avatar)
        avatar.refresh_from_db()
        _raise_if_avatar_failed(avatar)
        out_serializer = AvatarSerializer(avatar, context=self.get_serializer_context())
        headers = self.get_success_headers(serializer.data)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


@extend_schema(
    summary="Get Current Default Avatar",
    description=(
        "Retrieves the user's current default avatar. Returns the user's preferred completed avatar if one exists. "
        "Otherwise, falls back to the admin-managed system default matching the user's gender and requested style."
    ),
    parameters=[
        OpenApiParameter(
            name="style",
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
            enum=Avatar.Style.values,
            description="Avatar style: 'realistic' or 'cartoon'",
        ),
    ],
    responses={
        200: AvatarSerializer,
        400: OpenApiResponse(description="Bad Request: Missing or invalid 'style' query parameter."),
    },
)
class AvatarDefaultView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_object(self):
        style_param = self.request.query_params.get("style")
        if not style_param or style_param not in Avatar.Style.values:
            raise serializers.ValidationError(
                {"style": f"The 'style' query parameter is required and must be one of: {', '.join(Avatar.Style.values)}."}
            )
        return resolve_user_default_avatar(self.request.user, requested_style=style_param)


class AvatarStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_queryset(self):
        return Avatar.objects.filter(user=self.request.user, is_default=False)

    def get_object(self):
        obj = super().get_object()
        synced = sync_avatar_status(obj)
        _raise_if_avatar_failed(synced)
        return synced


class AvatarListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_queryset(self):
        return Avatar.objects.filter(user=self.request.user, is_default=False)


class AvatarSaveView(generics.GenericAPIView):
    """
    On-demand Cloudinary save endpoint for completed avatars.
    Downloads the fal.ai result from fal_cdn_url, uploads it to Cloudinary (saving to result_image),
    sets is_saved=True, and returns the persistent Cloudinary URL.
    Idempotent: if already saved, returns the saved avatar without re-uploading.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_queryset(self):
        return Avatar.objects.filter(user=self.request.user, is_default=False)

    def post(self, request, *args, **kwargs):
        avatar = self.get_object()
        _raise_if_avatar_failed(avatar)

        if avatar.status != Avatar.JobStatus.DONE:
            raise ServiceError(
                detail="Avatar generation is not completed yet.",
                status_code=400,
            )

        if not avatar.is_saved:
            if not avatar.fal_cdn_url:
                raise ServiceError(
                    detail="No generated avatar image available to save.",
                    status_code=400,
                )

            try:
                img_resp = requests.get(avatar.fal_cdn_url, timeout=30)
                img_resp.raise_for_status()

                filename = f"avatar_{avatar.id}.png"
                avatar.result_image.save(filename, ContentFile(img_resp.content), save=False)
                avatar.is_saved = True
                avatar.save()
            except Exception as exc:
                raw_error = str(exc)
                raise ServiceError(
                    detail="Failed to save avatar image to cloud storage.",
                    debug_detail=raw_error,
                    status_code=502,
                )

        serializer = self.get_serializer(avatar)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SystemDefaultAvatarAdminView(generics.ListCreateAPIView):
    """
    Admin-only endpoint for managing system default avatars (male/female x realistic/cartoon).
    GET: Returns all configured system default avatars.
    POST: Uploads the provided image to Cloudinary and updates or creates the system default
          for the specified (gender, style) combination.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        return Avatar.objects.filter(is_default=True)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SystemDefaultAvatarAdminSerializer
        return AvatarSerializer

    @extend_schema(responses={200: AvatarSerializer(many=True)})
    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = AvatarSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(request=SystemDefaultAvatarAdminSerializer, responses={200: AvatarSerializer})
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        gender = serializer.validated_data["gender"]
        style = serializer.validated_data["style"]
        image = serializer.validated_data["image"]

        default_avatar = Avatar.objects.filter(
            is_default=True, gender=gender, style=style
        ).first()

        if not default_avatar:
            default_avatar = Avatar.objects.create(
                user=None,
                is_default=True,
                gender=gender,
                style=style,
                status=Avatar.JobStatus.DONE,
                is_saved=True,
            )

        filename = f"system_default_{gender}_{style}.png"
        default_avatar.result_image.save(filename, image, save=False)
        default_avatar.status = Avatar.JobStatus.DONE
        default_avatar.is_saved = True
        default_avatar.save()

        out_serializer = AvatarSerializer(default_avatar, context=self.get_serializer_context())
        return Response(out_serializer.data, status=status.HTTP_200_OK)


