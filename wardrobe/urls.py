from django.urls import path

from wardrobe_items_ai.views import (
    WardrobeItemDetailView,
    WardrobeItemListCreateView,
    WardrobeItemSaveView,
)
from . import views

urlpatterns = [
    path("options/", views.WardrobeOptionsView.as_view(), name="wardrobe-options"),
    path("items/", WardrobeItemListCreateView.as_view(), name="wardrobe-items"),
    path("items/<int:pk>/", WardrobeItemDetailView.as_view(), name="wardrobe-item-detail"),
    path("items/<int:pk>/save/", WardrobeItemSaveView.as_view(), name="wardrobe-item-save"),
]
