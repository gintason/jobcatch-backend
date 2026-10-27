"""
Paid job listings: publish date, expiry, featured flag, and the package
purchase that paid for the listing.

Existing jobs are grandfathered: published_at = created_at (so "Posted on"
shows their real date) and no expiry.
"""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models import F


def backfill_published_at(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    Job.objects.update(published_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0001_initial"),
        ("listings", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="job",
            name="is_featured",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="job",
            name="listing_purchase",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="jobs",
                to="listings.packagepurchase",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="published_at",
            field=models.DateTimeField(
                blank=True, db_index=True, default=django.utils.timezone.now, null=True
            ),
        ),
        migrations.RunPython(backfill_published_at, migrations.RunPython.noop),
    ]
