from django.contrib import admin
from .models import ThreeDConversion


@admin.register(ThreeDConversion)
class ThreeDConversionAdmin(admin.ModelAdmin):
    list_display = ("id", "outfit_job", "status", "fal_request_id", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "fal_request_id", "outfit_job__id", "outfit_job__user__email")
    readonly_fields = ("created_at", "updated_at")
