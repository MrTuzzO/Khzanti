from django.urls import path
from .views import TodayOutfitResetView, TodayOutfitView, TryOnCreateView, TryOnStatusView, TryOnWebhookView

app_name = "outfits"

urlpatterns = [
    path("today/", TodayOutfitView.as_view(), name="today-outfit"),
    path("today/reset/", TodayOutfitResetView.as_view(), name="today-outfit-reset"),
    path("try-on/", TryOnCreateView.as_view(), name="try-on-create"),
    path("try-on/<int:pk>/status/", TryOnStatusView.as_view(), name="try-on-status"),
    path("try-on/webhook/", TryOnWebhookView.as_view(), name="try-on-webhook"),
]
