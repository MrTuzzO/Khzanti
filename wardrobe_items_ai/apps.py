from django.apps import AppConfig


class WardrobeItemsAiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wardrobe_items_ai"
    verbose_name = "Wardrobe Items AI"

    def ready(self):
        import wardrobe_items_ai.signals  # noqa: F401

