from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Avatar(models.Model):
    class JobStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    class Style(models.TextChoices):
        REALISTIC = "realistic", _("Realistic")
        CARTOON = "cartoon", _("Cartoon")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="avatars",
        null=True,
        blank=True,
        db_index=True,
    )
    source_photo = models.ImageField(upload_to="avatars/source/", blank=True, null=True)
    style = models.CharField(
        max_length=20,
        choices=Style.choices,
        default=Style.REALISTIC,
        db_index=True,
    )
    is_default = models.BooleanField(default=False, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    fal_request_id = models.CharField(max_length=100, blank=True)
    result_image = models.ImageField(
        upload_to="avatars/result/",
        null=True,
        blank=True,
    )
    error_message = models.TextField(blank=True)
    internal_error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="unique_default_avatar",
            )
        ]

    def __str__(self):
        if self.is_default:
            return f"System Default Avatar {self.id}"
        return f"Avatar {self.id} - {self.user} ({self.style}, {self.status})"


