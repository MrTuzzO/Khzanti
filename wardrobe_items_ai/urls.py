from django.urls import path
from .views import (
    BgRemovalWebhookView,
    ItemAnalysisStatusView,
    ItemAnalysisTriggerView,
    VisionWebhookView,
    WardrobeItemDetailView,
    WardrobeItemListCreateView,
)

app_name = "wardrobe_items_ai"

urlpatterns = [
    path("items/", WardrobeItemListCreateView.as_view(), name="item-list-create"),
    path("items/<int:pk>/", WardrobeItemDetailView.as_view(), name="item-detail"),
    path("trigger/", ItemAnalysisTriggerView.as_view(), name="trigger"),
    path("<int:pk>/status/", ItemAnalysisStatusView.as_view(), name="status"),
    path("webhook/bg-removal/", BgRemovalWebhookView.as_view(), name="webhook-bg-removal"),
    path("webhook/vision/", VisionWebhookView.as_view(), name="webhook-vision"),
]
