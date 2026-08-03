from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import ItemAnalysis, WardrobeItem


@admin.register(WardrobeItem)
class WardrobeItemAdmin(ModelAdmin):
    list_display = ("id", "user", "category", "season", "occasion", "created_at")
    list_filter = ("category", "season", "occasion", "created_at")
    search_fields = ("purchase_source", "user__email")


@admin.register(ItemAnalysis)
class ItemAnalysisAdmin(ModelAdmin):
    list_display = ("id", "wardrobe_item", "status", "color", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "fal_request_id_bg_removal",
        "fal_request_id_vision",
        "internal_error_detail",
        "created_at",
        "updated_at",
    )
