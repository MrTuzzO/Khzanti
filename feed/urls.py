from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PostViewSet, RatePostView

router = DefaultRouter()
router.register(r'posts', PostViewSet, basename='post')

urlpatterns = [
    path('', include(router.urls)),
    path('posts/<int:pk>/rate/', RatePostView.as_view(), name='post-rate'),
]
