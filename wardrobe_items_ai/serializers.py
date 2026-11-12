import io
import logging
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework import serializers

from core.validators import RestrictedImageField
from wardrobe.models import Category
from wardrobe.serializers import CategorySerializer
from .models import ItemAnalysis, WardrobeItem

logger = logging.getLogger(__name__)


def apply_exif_orientation(image_file):
    """
    Inspects image EXIF data and applies ImageOps.exif_transpose() so mobile camera
    photos are correctly oriented upright before saving and AI processing.
    Preserves already-correct images and handles exceptions safely.
    """
    if not image_file:
        return image_file
    try:
        from PIL import Image, ImageOps
        from django.core.files.uploadedfile import SimpleUploadedFile

        img = Image.open(image_file)
        exif = getattr(img, "getexif", None)
        orientation = exif().get(0x0112) if (exif and callable(exif)) else None
        if not orientation and hasattr(img, "_getexif") and callable(img._getexif):
            raw_exif = img._getexif()
            if raw_exif:
                orientation = raw_exif.get(0x0112)

        if orientation in (2, 3, 4, 5, 6, 7, 8):
            transposed = ImageOps.exif_transpose(img)
            if transposed is not None:
                buf = io.BytesIO()
                save_fmt = img.format or "JPEG"
                transposed.save(buf, format=save_fmt)
                buf.seek(0)
                return SimpleUploadedFile(
                    name=getattr(image_file, "name", "upload.jpg"),
                    content=buf.getvalue(),
                    content_type=getattr(image_file, "content_type", "image/jpeg"),
                )
    except Exception as exc:
        logger.warning("EXIF orientation processing skipped for %s: %s", getattr(image_file, "name", "file"), exc)
    finally:
        if hasattr(image_file, "seek") and callable(image_file.seek):
            image_file.seek(0)
    return image_file


class NullableScalarModelSerializerMixin:
    """
    Mixin for ModelSerializer classes to normalize empty scalar string
    values ("") into None (JSON null) in API response representations.
    Preserves list/collection fields as empty arrays [], dicts as {},
    booleans, numbers, and non-empty strings.
    """

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if isinstance(ret, dict):
            for key, val in list(ret.items()):
                if val == "":
                    ret[key] = None
        return ret


class WardrobeItemCreateSerializer(NullableScalarModelSerializerMixin, serializers.ModelSerializer):
    image = RestrictedImageField(required=True)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        help_text="Category ID (integer)."
    )

    class Meta:
        model = WardrobeItem
        fields = [
            "id",
            "image",
            "category",
            "season",
            "occasion",
            "purchase_source",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_image(self, value):
        return apply_exif_orientation(value)



class ItemAnalysisSerializer(NullableScalarModelSerializerMixin, serializers.ModelSerializer):
    display_url = serializers.ReadOnlyField()
    processed_image = serializers.SerializerMethodField()

    class Meta:
        model = ItemAnalysis
        fields = [
            "id",
            "wardrobe_item",
            "status",
            "is_saved",
            "display_url",
            "fal_cdn_url",
            "processed_image",
            "color",
            "description",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.STR)
    def get_processed_image(self, obj):
        if obj.is_saved and obj.processed_image:
            try:
                return obj.processed_image.url
            except Exception:
                pass
        return None



class WardrobeItemSerializer(NullableScalarModelSerializerMixin, serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    analysis = ItemAnalysisSerializer(read_only=True)

    class Meta:
        model = WardrobeItem
        fields = [
            "id",
            "image",
            "category",
            "season",
            "occasion",
            "purchase_source",
            "analysis",
            "created_at",
        ]
        read_only_fields = fields
