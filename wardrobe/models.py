from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Season(models.TextChoices):
    SUMMER = "summer", "Summer"
    WINTER = "winter", "Winter"
    SPRING = "spring", "Spring"
    AUTUMN = "autumn", "Autumn"
    ALL_SEASON = "all_season", "All Season"


class Occasion(models.TextChoices):
    CASUAL = "casual", "Casual"
    FORMAL = "formal", "Formal"
    PARTY = "party", "Party"
    SPORT = "sport", "Sport"
    WEDDING = "wedding", "Wedding"
    WORK = "work", "Work"
