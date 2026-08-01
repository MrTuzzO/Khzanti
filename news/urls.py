from django.urls import path

from . import views

urlpatterns = [
    path("", views.PostListView.as_view(), name="news-list"),
    path("<slug:slug>/", views.PostDetailView.as_view(), name="news-detail"),
]
