"""
Add human-readable location fields (city/area/state) to ArtisanProfile and
bump the default + existing service radius to 25 km.

- city/area/state are optional free-text so an artisan can say e.g.
  Lagos (city) / Ikeja (area) / Lagos State (state); shown to customers.
- service_radius_km default goes 10 -> 25, and existing artisans still on the
  old default (or below 25) are raised to 25 so the wider 25 km search radius
  actually surfaces them.
"""
from django.db import migrations, models


def bump_existing_radius(apps, schema_editor):
    ArtisanProfile = apps.get_model("accounts", "ArtisanProfile")
    # Raise anyone below 25 up to 25; leave artisans who chose a larger radius.
    ArtisanProfile.objects.filter(service_radius_km__lt=25).update(service_radius_km=25)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_merge_20260716_2352"),
    ]

    operations = [
        migrations.AddField(
            model_name="artisanprofile",
            name="city",
            field=models.CharField(max_length=100, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="artisanprofile",
            name="area",
            field=models.CharField(max_length=100, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="artisanprofile",
            name="state",
            field=models.CharField(max_length=100, blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="artisanprofile",
            name="service_radius_km",
            field=models.PositiveIntegerField(default=25),
        ),
        migrations.RunPython(bump_existing_radius, noop_reverse),
    ]
