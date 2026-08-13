from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Follow


@admin.register(Follow)
class FollowAdmin(ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__email", "follower__username", "following__email", "following__username")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("follower", "following")
