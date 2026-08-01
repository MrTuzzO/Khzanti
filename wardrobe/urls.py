from django.urls import path

from . import views

urlpatterns = [
    path("options/", views.WardrobeOptionsView.as_view(), name="wardrobe-options"),
]
