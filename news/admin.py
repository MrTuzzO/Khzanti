from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import Post


@admin.register(Post)
class PostAdmin(ModelAdmin):
    list_display = ("thumbnail", "title", "is_published", "created_at")
    list_filter = ("is_published",)
    search_fields = ("title",)
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Image")
    def thumbnail(self, obj):
        if not obj.featured_image:
            return "—"
        return format_html(
            '<img src="{}" style="width:40px;height:40px;object-fit:cover;border-radius:6px;" />',
            obj.featured_image.url,
        )
