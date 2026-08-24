import logging
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import ItemAnalysis, WardrobeItem

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=WardrobeItem)
def auto_delete_wardrobe_item_image_on_delete(sender, instance, **kwargs):
    """
    Deletes original wardrobe item image from Cloudinary storage when WardrobeItem is deleted.
    """
    if instance.image:
        try:
            instance.image.delete(save=False)
            logger.info("Deleted Cloudinary original image for WardrobeItem %s", instance.id)
        except Exception as exc:
            logger.warning("Failed to delete Cloudinary original image for WardrobeItem %s: %s", instance.id, exc)


@receiver(post_delete, sender=ItemAnalysis)
def auto_delete_item_analysis_processed_image_on_delete(sender, instance, **kwargs):
    """
    Deletes processed wardrobe item image from Cloudinary storage when ItemAnalysis is deleted.
    """
    if instance.processed_image:
        try:
            instance.processed_image.delete(save=False)
            logger.info("Deleted Cloudinary processed image for ItemAnalysis %s", instance.id)
        except Exception as exc:
            logger.warning("Failed to delete Cloudinary processed image for ItemAnalysis %s: %s", instance.id, exc)
