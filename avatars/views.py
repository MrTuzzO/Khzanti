from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ServiceError
from .models import Avatar
from .serializers import AvatarCreateSerializer, AvatarSerializer
from .services import submit_avatar_job, sync_avatar_status


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


class AvatarDefaultView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_object(self):
        default_avatar = Avatar.objects.filter(is_default=True).first()
        if not default_avatar:
            default_avatar = Avatar.objects.create(
                user=None,
                is_default=True,
                status=Avatar.JobStatus.DONE,
                style=Avatar.Style.REALISTIC,
                result_image="avatars/result/default_avatar.png",  #change the path later
            )
        return default_avatar


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


class AvatarSaveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: AvatarSerializer})
    def post(self, request, pk=None, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        from .services import save_avatar_to_cloudinary

        avatar = get_object_or_404(Avatar, pk=pk, user=request.user, is_default=False)
        avatar = sync_avatar_status(avatar)
        _raise_if_avatar_failed(avatar)

        if avatar.status != Avatar.JobStatus.DONE:
            raise ServiceError(
                detail="Avatar generation is not completed yet.",
                status_code=400,
            )

        saved_avatar = save_avatar_to_cloudinary(avatar)
        serializer = AvatarSerializer(saved_avatar, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)



