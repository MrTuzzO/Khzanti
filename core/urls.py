from django.urls import path
from . import views

urlpatterns = [
    path("settings/", views.SiteSettingsView.as_view(), name="site-settings"),
    path("brand-new/", views.BrandNewListCreateView.as_view(), name="brand-new-list-create"),
    path("brand-new/<int:pk>/", views.BrandNewDetailView.as_view(), name="brand-new-detail"),
]
