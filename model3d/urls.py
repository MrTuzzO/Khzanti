from django.urls import path
from .views import ConversionDetailView, ConversionWebhookView, ConvertCreateView

app_name = "model3d"

urlpatterns = [
    path("convert/", ConvertCreateView.as_view(), name="convert"),
    path("conversions/<int:pk>/", ConversionDetailView.as_view(), name="conversion-detail"),
    path("webhook/", ConversionWebhookView.as_view(), name="webhook"),
]
