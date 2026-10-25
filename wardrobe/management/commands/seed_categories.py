from django.core.management.base import BaseCommand
from wardrobe.models import Category


DEFAULT_CATEGORIES = [
    "Kaftan",
    "Bag",
    "Hijab",
    "Borkha",
    "Tops",
    "Outerwear",
    "Shoes",
    "Shirt",
    "Dress",
    "Pant",
    "T-Shirt",
    "Accessories",
]


class Command(BaseCommand):
    help = "Seeds initial wardrobe categories into the database safely without duplicating existing ones."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Seeding Wardrobe Categories ==="))

        created_count = 0
        for name in DEFAULT_CATEGORIES:
            category, created = Category.objects.get_or_create(
                name=name,
                defaults={"is_active": True},
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  [CREATED] ID {category.id}: {category.name}"))
            else:
                self.stdout.write(f"  [EXISTS]  ID {category.id}: {category.name}")

        total = Category.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSuccessfully seeded categories. Created: {created_count}, Total active categories: {total}."
            )
        )
