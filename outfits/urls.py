from django.urls import path
from .views import (
    PublicSavedOutfitListView,
    RateSavedOutfitView,
    SavedOutfitDetailView,
    SavedOutfitListCreateView,
    SavedOutfitRatingsListView,
    TodayOutfitResetView,
    TodayOutfitView,
    TryOnCreateView,
    TryOnStatusView,
    TryOnWebhookView,
)

app_name = "outfits"

urlpatterns = [
    path("today/", TodayOutfitView.as_view(), name="today-outfit"),
    path("today/reset/", TodayOutfitResetView.as_view(), name="today-outfit-reset"),
    path("try-on/", TryOnCreateView.as_view(), name="try-on-create"),
    path("try-on/<int:pk>/status/", TryOnStatusView.as_view(), name="try-on-status"),
    path("try-on/webhook/", TryOnWebhookView.as_view(), name="try-on-webhook"),
    path("saved/", SavedOutfitListCreateView.as_view(), name="saved-outfit-list-create"),
    path("saved/<int:pk>/", SavedOutfitDetailView.as_view(), name="saved-outfit-detail"),
    path("saved/<int:pk>/rate/", RateSavedOutfitView.as_view(), name="saved-outfit-rate"),
    path("saved/<int:pk>/ratings/", SavedOutfitRatingsListView.as_view(), name="saved-outfit-ratings-list"),
    path("public/<str:username>/", PublicSavedOutfitListView.as_view(), name="public-saved-outfits"),
]

