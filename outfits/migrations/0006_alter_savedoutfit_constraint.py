from django.db import migrations, models


def repair_saved_outfits(apps, schema_editor):
    OutfitJob = apps.get_model('outfits', 'OutfitJob')
    SavedOutfit = apps.get_model('outfits', 'SavedOutfit')

    saved_jobs = OutfitJob.objects.filter(is_saved=True)
    for job in saved_jobs:
        if job.user and not SavedOutfit.objects.filter(user=job.user, outfit_job=job).exists():
            target_date = job.scheduled_date
            if not target_date and hasattr(job, 'created_at') and job.created_at:
                target_date = job.created_at.date()
            if not target_date:
                from django.utils import timezone
                target_date = timezone.now().date()
            SavedOutfit.objects.get_or_create(
                user=job.user,
                outfit_job=job,
                defaults={"date": target_date},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('outfits', '0005_alter_outfitjob_fal_cdn_url'),
        ('outfits', '0005_merge_20260813_1015'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='savedoutfit',
            name='unique_saved_outfit_per_user_per_date',
        ),
        migrations.AddConstraint(
            model_name='savedoutfit',
            constraint=models.UniqueConstraint(fields=('user', 'outfit_job'), name='unique_saved_outfit_per_user_per_job'),
        ),
        migrations.RunPython(repair_saved_outfits, reverse_code=migrations.RunPython.noop),
    ]
