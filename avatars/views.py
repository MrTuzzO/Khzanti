from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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
        avatar = serializer.save(user=request.user, status=Avatar.JobStatus.PENDING)
        submit_avatar_job(avatar)
        avatar.refresh_from_db()
        _raise_if_avatar_failed(avatar)
        out_serializer = AvatarSerializer(avatar, context=self.get_serializer_context())
        headers = self.get_success_headers(serializer.data)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class AvatarStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_queryset(self):
        return Avatar.objects.filter(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        synced = sync_avatar_status(obj)
        _raise_if_avatar_failed(synced)
        return synced


class AvatarListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AvatarSerializer

    def get_queryset(self):
        return Avatar.objects.filter(user=self.request.user)

