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
    GenderChoices = [
        ("male", _("Male")),
        ("female", _("Female")),
        ("other", _("Other")),
    ]

    gender = models.CharField(
        max_length=10,
        choices=GenderChoices,
        blank=True,
        null=True,
        db_index=True,
    )
    is_default = models.BooleanField(default=False, db_index=True)
    is_preferred = models.BooleanField(default=False, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    fal_request_id = models.CharField(max_length=100, blank=True)
    fal_cdn_url = models.URLField(max_length=1024, blank=True, null=True)
    result_image = models.ImageField(
        upload_to="avatars/result/",
        null=True,
        blank=True,
    )
    is_saved = models.BooleanField(default=False, db_index=True)
    error_message = models.TextField(blank=True)
    internal_error_detail = models.TextField(blank=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["is_default", "gender", "style"],
                condition=models.Q(is_default=True),
                name="unique_system_default_avatar_per_gender_style",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_preferred=True),
                name="unique_preferred_avatar_per_user",
            ),
        ]

    def __str__(self):
        if self.is_default:
            return f"System Default Avatar ({self.gender}, {self.style}) #{self.id}"
        pref = " [Preferred]" if self.is_preferred else ""
        return f"Avatar #{self.id}{pref} - {self.user} ({self.style}, {self.status})"


