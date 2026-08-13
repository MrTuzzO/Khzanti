import re

from django.db import migrations, models


def backfill_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    existing = set()
    for user in User.objects.all().order_by("id"):
        base = re.sub(r"[^a-z0-9_]", "", user.email.split("@")[0].lower())[:25] or "user"
        username = base
        suffix = 0
        while username in existing or User.objects.filter(username=username).exclude(pk=user.pk).exists():
            suffix += 1
            username = f"{base}{suffix}"
        existing.add(username)
        user.username = username
        user.save(update_fields=["username"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_aesthetic_customerprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='username',
            field=models.CharField(max_length=30, null=True, blank=True),
        ),
        migrations.RunPython(backfill_usernames, noop),
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(max_length=30, unique=True, db_index=True),
        ),
    ]
