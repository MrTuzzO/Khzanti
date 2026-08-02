from django.urls import path

from .views import AvatarCreateView, AvatarListView, AvatarStatusView

app_name = "avatars"

urlpatterns = [
    path("", AvatarCreateView.as_view(), name="avatar-create"),
    path("mine/", AvatarListView.as_view(), name="avatar-list"),
    path("<int:pk>/status/", AvatarStatusView.as_view(), name="avatar-status"),
]
