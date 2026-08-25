from django.contrib import admin
from .models import DailyOutfitSelection, OutfitJob, OutfitRating, SavedOutfit


@admin.register(OutfitJob)
class OutfitJobAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "avatar", "status", "trigger_type", "created_at"]
    list_filter = ["status", "trigger_type", "created_at"]
    search_fields = ["user__email", "fal_request_id", "id"]
    filter_horizontal = ["wardrobe_items"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SavedOutfit)
class SavedOutfitAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "outfit_job", "date", "is_shared", "created_at"]
    list_filter = ["is_shared", "date", "created_at"]
    search_fields = ["user__email", "id"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["outfit_job"]


@admin.register(OutfitRating)
class OutfitRatingAdmin(admin.ModelAdmin):
    list_display = ["id", "saved_outfit", "rater", "color_harmony", "trendy", "overall_matching", "accessories", "created_at"]
    search_fields = ["rater__email", "saved_outfit__user__email"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["saved_outfit", "rater"]


@admin.register(DailyOutfitSelection)
class DailyOutfitSelectionAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "date", "outfit_job", "created_at"]
    list_filter = ["date", "created_at"]
    search_fields = ["user__email", "id"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["outfit_job"]


