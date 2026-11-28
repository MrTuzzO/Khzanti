from django.contrib import admin, messages
from social.models import ReportStatus
from .models import Post, PostImage, PostRating, PostReport, OutfitReport

class PostImageInline(admin.TabularInline):
    model = PostImage
    extra = 1

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "privacy", "created_at")
    list_filter = ("privacy", "created_at")
    search_fields = ("user__username", "user__email", "caption")
    inlines = [PostImageInline]
    readonly_fields = ("created_at", "updated_at")

@admin.register(PostRating)
class PostRatingAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "rater", "color_harmony", "trendy", "overall_matching", "accessories", "created_at")
    list_filter = ("created_at",)
    search_fields = ("rater__username", "post__id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PostReport)
class PostReportAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "post_author", "reporter", "reason", "status", "created_at")
    list_filter = ("status", "reason", "created_at")
    search_fields = ("post__id", "post__user__username", "reporter__username", "details")
    readonly_fields = ("reporter", "post", "reason", "details", "created_at")
    actions = ["mark_reviewed", "mark_dismissed", "delete_reported_posts"]

    @admin.display(description="Post author")
    def post_author(self, obj):
        return obj.post.user

    @admin.action(description="Mark selected reports as reviewed")
    def mark_reviewed(self, request, queryset):
        queryset.update(status=ReportStatus.REVIEWED)

    @admin.action(description="Dismiss selected reports")
    def mark_dismissed(self, request, queryset):
        queryset.update(status=ReportStatus.DISMISSED)

    @admin.action(description="Delete the reported posts (permanent)")
    def delete_reported_posts(self, request, queryset):
        post_ids = list(queryset.values_list("post_id", flat=True))
        count = Post.objects.filter(pk__in=post_ids).count()
        Post.objects.filter(pk__in=post_ids).delete()
        self.message_user(request, f"Deleted {count} post(s).", messages.SUCCESS)


@admin.register(OutfitReport)
class OutfitReportAdmin(admin.ModelAdmin):
    list_display = ("id", "saved_outfit", "outfit_owner", "reporter", "reason", "status", "created_at")
    list_filter = ("status", "reason", "created_at")
    search_fields = ("saved_outfit__id", "saved_outfit__user__username", "reporter__username", "details")
    readonly_fields = ("reporter", "saved_outfit", "reason", "details", "created_at")
    actions = ["mark_reviewed", "mark_dismissed", "delete_reported_outfits"]

    @admin.display(description="Outfit owner")
    def outfit_owner(self, obj):
        return obj.saved_outfit.user

    @admin.action(description="Mark selected reports as reviewed")
    def mark_reviewed(self, request, queryset):
        queryset.update(status=ReportStatus.REVIEWED)

    @admin.action(description="Dismiss selected reports")
    def mark_dismissed(self, request, queryset):
        queryset.update(status=ReportStatus.DISMISSED)

    @admin.action(description="Delete the reported outfits (permanent)")
    def delete_reported_outfits(self, request, queryset):
        from outfits.models import SavedOutfit
        ids = list(queryset.values_list("saved_outfit_id", flat=True))
        count = SavedOutfit.objects.filter(pk__in=ids).count()
        SavedOutfit.objects.filter(pk__in=ids).delete()
        self.message_user(request, f"Deleted {count} outfit(s).", messages.SUCCESS)
