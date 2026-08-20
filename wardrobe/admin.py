from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Category


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
