from django.http import Http404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ItemAnalysis, WardrobeItem
from .serializers import ItemAnalysisSerializer
from .services import submit_analysis_job, sync_analysis_status


class ItemAnalysisTriggerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        wardrobe_item_id = request.data.get("wardrobe_item_id") or request.data.get("wardrobe_item")
        if not wardrobe_item_id:
            return Response(
                {"wardrobe_item_id": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            item = WardrobeItem.objects.get(pk=wardrobe_item_id)
        except WardrobeItem.DoesNotExist:
            raise Http404("Wardrobe item not found.")

        # Ownership check: must belong to request.user
        if item.user != request.user:
            raise Http404("Wardrobe item not found.")

        analysis, created = ItemAnalysis.objects.get_or_create(wardrobe_item=item)

        # If analysis already exists and failed, reset status for retry
        if not created and analysis.status == ItemAnalysis.JobStatus.FAILED:
            analysis.status = ItemAnalysis.JobStatus.PENDING
            analysis.error_message = ""
            analysis.internal_error_detail = ""
            analysis.save()

        submit_analysis_job(analysis)
        analysis.refresh_from_db()

        serializer = ItemAnalysisSerializer(analysis)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ItemAnalysisStatusView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ItemAnalysisSerializer

    def get_queryset(self):
        return ItemAnalysis.objects.filter(wardrobe_item__user=self.request.user)

    def get_object(self):
        analysis = super().get_object()
        return sync_analysis_status(analysis)
