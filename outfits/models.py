from django.conf import settings
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
    result_image = models.ImageField(
        upload_to="outfits/result/",
        null=True,
        blank=True,
    )
    error_message = models.TextField(blank=True)
    internal_error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

