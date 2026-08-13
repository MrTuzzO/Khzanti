from django.urls import path

from . import views

app_name = "social"

urlpatterns = [
    path("search/", views.UserSearchView.as_view(), name="user-search"),
    path("users/<str:username>/", views.PublicProfileView.as_view(), name="public-profile"),
    path("users/<str:username>/follow/", views.FollowActionView.as_view(), name="follow-toggle"),
    path("users/<str:username>/followers/", views.FollowersListView.as_view(), name="followers-list"),
    path("users/<str:username>/following/", views.FollowingListView.as_view(), name="following-list"),
]
