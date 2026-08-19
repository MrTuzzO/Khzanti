from django.conf import settings
from django.db import models
from wardrobe.models import Category, Season, Occasion


class JobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class WardrobeItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wardrobe_items",
    )
    image = models.ImageField(upload_to="wardrobe_items/originals/")
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="items",
    )
    season = models.CharField(
        max_length=50,
        choices=Season.choices,
        blank=True,
    )
    occasion = models.CharField(
        max_length=50,
        choices=Occasion.choices,
        blank=True,
    )
    purchase_source = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Wardrobe Item"
        verbose_name_plural = "Wardrobe Items"
        ordering = ["-created_at"]

    def __str__(self):
        return f"WardrobeItem {self.pk} - {self.user}"


class ItemAnalysis(models.Model):
    JobStatus = JobStatus

    wardrobe_item = models.OneToOneField(
        WardrobeItem,
        on_delete=models.CASCADE,
        related_name="analysis",
    )
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        db_index=True,
    )
    processed_image = models.ImageField(
        upload_to="wardrobe_items_ai/processed/",
        null=True,
        blank=True,
    )
    fal_cdn_url = models.URLField(max_length=1024, blank=True, default="")
    is_saved = models.BooleanField(default=False, db_index=True)
    color = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    fal_request_id_bg_removal = models.CharField(max_length=100, blank=True)
    fal_request_id_vision = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    internal_error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Item Analysis"
        verbose_name_plural = "Item Analyses"

    @property
    def display_url(self) -> str:
        if self.is_saved and self.processed_image:
            try:
                return self.processed_image.url
            except Exception:
                pass
        return self.fal_cdn_url or (self.processed_image.url if self.processed_image else "")

    def __str__(self):
        return f"Analysis {self.pk} - {self.wardrobe_item}"

