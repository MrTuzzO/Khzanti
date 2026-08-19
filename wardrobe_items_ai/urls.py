from django.urls import path
from .views import (
    BgRemovalWebhookView,
    ItemAnalysisStatusView,
    VisionWebhookView,
    WardrobeItemDetailView,
    WardrobeItemListCreateView,
    WardrobeItemSaveView,
)

app_name = "wardrobe_items_ai"

urlpatterns = [
    path("items/", WardrobeItemListCreateView.as_view(), name="item-list-create"),
    path("items/<int:pk>/", WardrobeItemDetailView.as_view(), name="item-detail"),
    path("items/<int:pk>/save/", WardrobeItemSaveView.as_view(), name="item-save"),
    path("<int:pk>/status/", ItemAnalysisStatusView.as_view(), name="status"),
    path("webhook/bg-removal/", BgRemovalWebhookView.as_view(), name="webhook-bg-removal"),
    path("webhook/vision/", VisionWebhookView.as_view(), name="webhook-vision"),
]
