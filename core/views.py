from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BrandNew, SiteSettings
from .pagination import StandardPagination
from .serializers import BrandNewSerializer, SiteSettingsSerializer


class SiteSettingsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: SiteSettingsSerializer})
    def get(self, request):
        return Response(SiteSettingsSerializer(SiteSettings.get_solo()).data)


class BrandNewListCreateView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StandardPagination

    @extend_schema(
        summary="List all brand new items",
        responses={200: BrandNewSerializer(many=True)},
    )
    def get(self, request):
        items = BrandNew.objects.all()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(items, request, view=self)
        if page is not None:
            serializer = BrandNewSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)

        serializer = BrandNewSerializer(items, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Create a new brand new item",
        request=BrandNewSerializer,
        responses={201: BrandNewSerializer},
    )
    def post(self, request):
        serializer = BrandNewSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BrandNewDetailView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self, pk):
        return get_object_or_404(BrandNew, pk=pk)

    @extend_schema(
        summary="Retrieve brand new item details",
        responses={200: BrandNewSerializer},
    )
    def get(self, request, pk):
        item = self.get_object(pk)
        serializer = BrandNewSerializer(item, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Update brand new item",
        request=BrandNewSerializer,
        responses={200: BrandNewSerializer},
    )
    def put(self, request, pk):
        item = self.get_object(pk)
        serializer = BrandNewSerializer(item, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Partial update brand new item",
        request=BrandNewSerializer,
        responses={200: BrandNewSerializer},
    )
    def patch(self, request, pk):
        item = self.get_object(pk)
        serializer = BrandNewSerializer(item, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Delete brand new item",
        responses={204: None},
    )
    def delete(self, request, pk):
        item = self.get_object(pk)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
