from django.contrib import admin
from solo.admin import SingletonModelAdmin
from unfold.admin import ModelAdmin

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(SingletonModelAdmin, ModelAdmin):
    fieldsets = (
        ("Legal Pages", {"fields": ("privacy_policy", "about_us", "terms_and_conditions")}),
        ("Contact Info", {"fields": ("contact_email", "contact_phone", "address")}),
        ("Social Links", {"fields": (
            "facebook_url", "instagram_url", "tiktok_url",
            "youtube_url", "twitter_url", "linkedin_url",
        )}),
        ("Meta", {"fields": ("updated_at",)}),
    )
    readonly_fields = ("updated_at",)
