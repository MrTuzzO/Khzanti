from rest_framework import generics
from rest_framework.permissions import AllowAny

from .models import Post
from .serializers import PostDetailSerializer, PostListSerializer


class PostListView(generics.ListAPIView):
    queryset = Post.objects.filter(is_published=True)
    serializer_class = PostListSerializer
    permission_classes = [AllowAny]


class PostDetailView(generics.RetrieveAPIView):
    queryset = Post.objects.filter(is_published=True)
    serializer_class = PostDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
