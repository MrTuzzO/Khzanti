from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from .models import Block, Follow, ReportStatus, UserReport


@admin.register(Follow)
class FollowAdmin(ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__email", "follower__username", "following__email", "following__username")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("follower", "following")


@admin.register(Block)
class BlockAdmin(ModelAdmin):
    list_display = ("blocker", "blocked", "created_at")
    search_fields = ("blocker__email", "blocker__username", "blocked__email", "blocked__username")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("blocker", "blocked")


@admin.register(UserReport)
class UserReportAdmin(ModelAdmin):
    list_display = ("id", "reported_user", "reporter", "reason", "status", "created_at")
    list_filter = ("status", "reason", "created_at")
    search_fields = (
        "reported_user__username", "reported_user__email",
        "reporter__username", "reporter__email", "details",
    )
    readonly_fields = ("reporter", "reported_user", "reason", "details", "created_at")
    actions = ["mark_reviewed", "mark_dismissed", "delete_reported_users"]

    @admin.action(description="Mark selected reports as reviewed")
    def mark_reviewed(self, request, queryset):
        queryset.update(status=ReportStatus.REVIEWED)

    @admin.action(description="Dismiss selected reports")
    def mark_dismissed(self, request, queryset):
        queryset.update(status=ReportStatus.DISMISSED)

    @admin.action(description="Delete the reported users (permanent)")
    def delete_reported_users(self, request, queryset):
        user_ids = set(queryset.values_list("reported_user_id", flat=True))
        users = [u for u in {r.reported_user for r in queryset.select_related("reported_user")} if not u.is_superuser]
        skipped = len(user_ids) - len(users)
        for user in users:
            user.delete()
        self.message_user(request, f"Deleted {len(users)} user(s).", messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Skipped {skipped} superuser account(s).", messages.WARNING)
