from django.contrib import admin
from .models import OutfitJob, SavedOutfit


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

