from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ServiceError
from .models import ItemAnalysis, WardrobeItem
from .serializers import (
    ItemAnalysisSerializer,
    ItemAnalysisTriggerSerializer,
    WardrobeItemCreateSerializer,
    WardrobeItemSerializer,
)
from .services import submit_analysis_job, sync_analysis_status


def _raise_if_analysis_failed(analysis: ItemAnalysis) -> None:
    if analysis.status == ItemAnalysis.JobStatus.FAILED:
        is_category_mismatch = "category mismatch" in (analysis.internal_error_detail or "").lower()
        status_code = 422 if is_category_mismatch else 502
        raise ServiceError(
            detail=analysis.error_message or "Failed to analyze wardrobe item. Please try again later.",
            debug_detail=analysis.internal_error_detail,
            status_code=status_code,
        )


class WardrobeItemListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return WardrobeItemCreateSerializer
        return WardrobeItemSerializer

    def get_queryset(self):
        qs = WardrobeItem.objects.filter(user=self.request.user)

        category_param = self.request.query_params.get("category")
        season_param = self.request.query_params.get("season")
        occasion_param = self.request.query_params.get("occasion")

        if category_param:
            if category_param.isdigit():
                qs = qs.filter(category_id=int(category_param))
            else:
                qs = qs.filter(category__name__iexact=category_param) | qs.filter(category__slug__iexact=category_param)

        if season_param:
            qs = qs.filter(season__iexact=season_param)

        if occasion_param:
            qs = qs.filter(occasion__iexact=occasion_param)

        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        item = serializer.instance
        analysis, _ = ItemAnalysis.objects.get_or_create(wardrobe_item=item)
        submit_analysis_job(analysis)

        fresh_item = WardrobeItem.objects.select_related("category", "analysis").get(pk=item.pk)

        headers = self.get_success_headers(serializer.data)
        read_serializer = WardrobeItemSerializer(fresh_item, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)




class WardrobeItemDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WardrobeItemSerializer

    def get_queryset(self):
        return WardrobeItem.objects.filter(user=self.request.user)


class ItemAnalysisTriggerView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ItemAnalysisTriggerSerializer,
        responses={
            200: ItemAnalysisSerializer,
            201: ItemAnalysisSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        input_serializer = ItemAnalysisTriggerSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                input_serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        wardrobe_item_id = input_serializer.validated_data["wardrobe_item_id"]

        try:
            item = WardrobeItem.objects.get(pk=wardrobe_item_id)
        except (WardrobeItem.DoesNotExist, ValueError):
            raise Http404("Wardrobe item not found.")

        # Ownership check: must belong to request.user
        if item.user != request.user:
            raise Http404("Wardrobe item not found.")

        analysis, created = ItemAnalysis.objects.get_or_create(wardrobe_item=item)

        if not created:
            # Skip reprocessing if already DONE or currently PROCESSING
            if analysis.status in (ItemAnalysis.JobStatus.DONE, ItemAnalysis.JobStatus.PROCESSING):
                serializer = ItemAnalysisSerializer(analysis)
                return Response(serializer.data, status=status.HTTP_200_OK)

            # If analysis already exists and failed, reset status for retry
            if analysis.status == ItemAnalysis.JobStatus.FAILED:
                analysis.status = ItemAnalysis.JobStatus.PENDING
                analysis.error_message = ""
                analysis.internal_error_detail = ""
                analysis.save()

        submit_analysis_job(analysis)
        analysis.refresh_from_db()

        _raise_if_analysis_failed(analysis)

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
        synced = sync_analysis_status(analysis)
        _raise_if_analysis_failed(synced)
        return synced



