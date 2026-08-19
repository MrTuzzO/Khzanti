from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Avatar


@admin.register(Avatar)
class AvatarAdmin(ModelAdmin):
    list_display = ("id", "user", "gender", "style", "is_default", "is_preferred", "is_saved", "status", "created_at")
    list_filter = ("status", "style", "gender", "is_default", "is_preferred", "is_saved")
    search_fields = ("user__email", "user__username", "fal_request_id")
    readonly_fields = ("fal_request_id", "internal_error_detail", "created_at", "updated_at")

