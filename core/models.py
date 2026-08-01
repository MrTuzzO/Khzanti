from django_ckeditor_5.fields import CKEditor5Field
from django.db import models
from solo.models import SingletonModel


class SiteSettings(SingletonModel):
    privacy_policy = CKEditor5Field(blank=True)
    about_us = CKEditor5Field(blank=True)
    terms_and_conditions = CKEditor5Field(blank=True)

    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)

    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    tiktok_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"
