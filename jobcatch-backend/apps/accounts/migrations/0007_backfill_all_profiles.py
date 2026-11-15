"""
Backfill ALL FOUR role profiles for every existing user.

The platform moved to a single-account model: users no longer pick a role at
signup, they switch modes from the role hub. Every user therefore needs a
Customer, Artisan, Employer and JobSeeker profile so any mode works instantly.

Idempotent (get_or_create) and reversible-as-noop.
"""
from django.db import migrations


def backfill_all_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    models = {
        "customer": apps.get_model("accounts", "CustomerProfile"),
        "artisan": apps.get_model("accounts", "ArtisanProfile"),
        "employer": apps.get_model("accounts", "EmployerProfile"),
        "job_seeker": apps.get_model("accounts", "JobSeekerProfile"),
    }

    for user in User.objects.all().iterator():
        for key, Model in models.items():
            if Model.objects.filter(user=user).exists():
                continue
            kwargs = {"user": user}
            if key == "employer":
                kwargs["company_name"] = user.full_name or "Unnamed Company"
            Model.objects.create(**kwargs)


def noop_reverse(apps, schema_editor):
    # Deleting backfilled profiles could destroy data users have since edited.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_artisan_location_and_radius"),
    ]

    operations = [
        migrations.RunPython(backfill_all_profiles, noop_reverse),
    ]
