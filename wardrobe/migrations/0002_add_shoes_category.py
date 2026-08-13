from django.db import migrations


def add_shoes_category(apps, schema_editor):
    Category = apps.get_model("wardrobe", "Category")
    Category.objects.get_or_create(
        name="Shoes",
        defaults={"slug": "shoes", "is_active": True},
    )


def remove_shoes_category(apps, schema_editor):
    Category = apps.get_model("wardrobe", "Category")
    Category.objects.filter(name="Shoes").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("wardrobe", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_shoes_category, reverse_code=remove_shoes_category),
    ]
