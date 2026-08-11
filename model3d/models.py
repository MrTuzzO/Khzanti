from django.db import models
from django.utils.translation import gettext_lazy as _


class ConversionStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    DONE = "done", _("Done")
    FAILED = "failed", _("Failed")


class ThreeDConversion(models.Model):
    outfit_job = models.ForeignKey(
        "outfits.OutfitJob",
        on_delete=models.CASCADE,
        related_name="three_d_conversions",
    )
    status = models.CharField(
        max_length=20,
        choices=ConversionStatus.choices,
        default=ConversionStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True)
    fal_request_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
    )
    result_mesh_url = models.URLField(
        max_length=1000,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "3D Conversion"
        verbose_name_plural = "3D Conversions"
        constraints = [
            models.UniqueConstraint(
                fields=["outfit_job"],
                condition=models.Q(
                    status__in=[
                        ConversionStatus.PENDING,
                        ConversionStatus.PROCESSING,
                        ConversionStatus.DONE,
                    ]
                ),
                name="unique_active_or_done_conversion_per_outfit_job",
            )
        ]

    def __str__(self):
        return f"ThreeDConversion {self.id} - OutfitJob {self.outfit_job_id} ({self.status})"
