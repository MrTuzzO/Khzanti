from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class JobStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    DONE = "done", _("Done")
    FAILED = "failed", _("Failed")


class TriggerType(models.TextChoices):
    MANUAL = "manual", _("Manual")
    AUTO = "auto", _("Auto")


class OutfitJob(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outfit_jobs",
    )
    avatar = models.ForeignKey(
        "avatars.Avatar",
        on_delete=models.CASCADE,
        related_name="outfit_jobs",
    )
    wardrobe_items = models.ManyToManyField(
        "wardrobe_items_ai.WardrobeItem",
        related_name="outfit_jobs",
    )
    scheduled_date = models.DateField(
        default=timezone.now,
        db_index=True,
    )
    trigger_type = models.CharField(
        max_length=20,
        choices=TriggerType.choices,
        default=TriggerType.MANUAL,
    )
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    fal_request_id = models.CharField(max_length=100, blank=True, db_index=True)
    fal_cdn_url = models.URLField(max_length=1024, blank=True)
    is_saved = models.BooleanField(default=False, db_index=True)
    result_image = models.ImageField(
        upload_to="outfits/result/",
        null=True,
        blank=True,
    )
    error_message = models.TextField(blank=True)
    internal_error_detail = models.TextField(blank=True)
    reasoning_title = models.CharField(max_length=255, blank=True)
    reasoning_subtitle = models.CharField(max_length=255, blank=True)
    reasoning_items = models.JSONField(default=list, blank=True)
    reasoning_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def display_result_image(self) -> str:
        if self.result_image:
            try:
                return self.result_image.url
            except Exception:
                pass
        return self.fal_cdn_url or ""

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Outfit Job"
        verbose_name_plural = "Outfit Jobs"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scheduled_date", "trigger_type"],
                condition=models.Q(trigger_type="auto"),
                name="unique_auto_outfit_per_user_per_day",
            )
        ]

    def __str__(self):
        return f"OutfitJob {self.id} - {self.user} ({self.trigger_type}, {self.status})"


class SavedOutfit(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_outfits",
    )
    outfit_job = models.ForeignKey(
        "OutfitJob",
        on_delete=models.CASCADE,
        related_name="saved_entries",
    )
    date = models.DateField(db_index=True)
    note = models.TextField(blank=True)
    is_shared = models.BooleanField(
        default=False,
        db_index=True,
        help_text="When on, this saved outfit is visible on the user's public profile.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Saved Outfit"
        verbose_name_plural = "Saved Outfits"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date"],
                name="unique_saved_outfit_per_user_per_date",
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_shared", "-date"]),
        ]

    def __str__(self):
        return f"SavedOutfit {self.id} - {self.user} on {self.date}"


class OutfitRating(models.Model):
    RATING_VALIDATORS = [MinValueValidator(0), MaxValueValidator(10)]

    saved_outfit = models.ForeignKey(
        SavedOutfit,
        on_delete=models.CASCADE,
        related_name="ratings",
    )
    rater = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outfit_ratings",
    )
    color_harmony = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    trendy = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    overall_matching = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    accessories = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Outfit Rating"
        verbose_name_plural = "Outfit Ratings"
        constraints = [
            models.UniqueConstraint(
                fields=["saved_outfit", "rater"],
                name="unique_rating_per_user_per_outfit",
            ),
        ]

    def __str__(self):
        return f"Rating {self.id} by {self.rater_id} on SavedOutfit {self.saved_outfit_id}"

