from django.urls import path

from .views import AvatarCreateView, AvatarDefaultView, AvatarListView, AvatarSaveView, AvatarStatusView

app_name = "avatars"

urlpatterns = [
    path("", AvatarCreateView.as_view(), name="avatar-create"),
    path("default/", AvatarDefaultView.as_view(), name="avatar-default"),
    path("mine/", AvatarListView.as_view(), name="avatar-list"),
    path("<int:pk>/status/", AvatarStatusView.as_view(), name="avatar-status"),
    path("<int:pk>/save/", AvatarSaveView.as_view(), name="avatar-save"),
]

