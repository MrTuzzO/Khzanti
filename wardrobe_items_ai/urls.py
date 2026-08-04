from django.urls import path
from .views import (
    ItemAnalysisStatusView,
    ItemAnalysisTriggerView,
    WardrobeItemDetailView,
    WardrobeItemListCreateView,
)

app_name = "wardrobe_items_ai"

urlpatterns = [
    path("items/", WardrobeItemListCreateView.as_view(), name="item-list-create"),
    path("items/<int:pk>/", WardrobeItemDetailView.as_view(), name="item-detail"),
    path("trigger/", ItemAnalysisTriggerView.as_view(), name="trigger"),
    path("<int:pk>/status/", ItemAnalysisStatusView.as_view(), name="status"),
]

