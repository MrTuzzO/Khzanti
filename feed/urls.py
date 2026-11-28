from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    FriendsStyleFeedView,
    PostViewSet,
    RatePostView,
    ReportOutfitView,
    ReportPostView,
    SaveFriendOutfitView,
)

router = DefaultRouter()
router.register(r'posts', PostViewSet, basename='post')

urlpatterns = [
    path('friends-style/', FriendsStyleFeedView.as_view(), name='friends-style'),
    path('friends-style/save/<int:pk>/', SaveFriendOutfitView.as_view(), name='friends-style-save'),
    path('', include(router.urls)),
    path('outfits/<int:pk>/report/', ReportOutfitView.as_view(), name='outfit-report'),
    path('posts/<int:pk>/report/', ReportPostView.as_view(), name='post-report'),
    path('posts/<int:pk>/rate/', RatePostView.as_view(), name='post-rate'),
]
