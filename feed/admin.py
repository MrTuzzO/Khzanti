from django.contrib import admin
from .models import Post, PostImage, PostRating

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
