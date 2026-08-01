from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Category, Occasion, Season
from .serializers import CategorySerializer


class WardrobeOptionsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses={200: inline_serializer(
            name="WardrobeOptions",
            fields={
                "categories": CategorySerializer(many=True),
                "seasons": serializers.ListField(child=serializers.DictField()),
                "occasions": serializers.ListField(child=serializers.DictField()),
            },
        )}
    )
    def get(self, request):
        categories = Category.objects.filter(is_active=True)
        return Response({
            "categories": CategorySerializer(categories, many=True).data,
            "seasons": [{"value": value, "label": label} for value, label in Season.choices],
            "occasions": [{"value": value, "label": label} for value, label in Occasion.choices],
        })
