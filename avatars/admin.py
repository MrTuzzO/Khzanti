from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Avatar


@admin.register(Avatar)
class AvatarAdmin(ModelAdmin):
    list_display = ("id", "user", "status", "created_at", "updated_at")
    list_filter = ("status",)
    readonly_fields = ("fal_request_id", "created_at", "updated_at")
