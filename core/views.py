from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import SiteSettings
from .serializers import SiteSettingsSerializer


class SiteSettingsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: SiteSettingsSerializer})
    def get(self, request):
        return Response(SiteSettingsSerializer(SiteSettings.get_solo()).data)
