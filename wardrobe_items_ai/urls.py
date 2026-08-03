from django.urls import path
from .views import ItemAnalysisTriggerView, ItemAnalysisStatusView

app_name = "wardrobe_items_ai"

urlpatterns = [
    path("trigger/", ItemAnalysisTriggerView.as_view(), name="trigger"),
    path("<int:pk>/status/", ItemAnalysisStatusView.as_view(), name="status"),
]
