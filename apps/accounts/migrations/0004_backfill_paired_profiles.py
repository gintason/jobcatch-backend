"""
Backfill paired profiles for multi-role support.

Every existing user gets the SECOND profile in their capability pair so that
demand-side users (customer/employer) can both book and post jobs, and
supply-side users (artisan/job_seeker) can both offer services and apply —
without a 404 when a viewset accesses request.user.<paired_profile>.

    customer  -> ensure EmployerProfile
    employer  -> ensure CustomerProfile
    artisan   -> ensure JobSeekerProfile
    job_seeker-> ensure ArtisanProfile

Idempotent (get_or_create) and reversible-as-noop. Uses the historical model
registry so it stays correct regardless of later model changes.
"""
from django.db import migrations


def backfill_paired_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    CustomerProfile = apps.get_model("accounts", "CustomerProfile")
    EmployerProfile = apps.get_model("accounts", "EmployerProfile")
    ArtisanProfile = apps.get_model("accounts", "ArtisanProfile")
    JobSeekerProfile = apps.get_model("accounts", "JobSeekerProfile")

    # role value -> (paired profile model, extra defaults)
    pairing = {
        "customer": (EmployerProfile, "employer_profile"),
        "employer": (CustomerProfile, "customer_profile"),
        "artisan": (JobSeekerProfile, "job_seeker_profile"),
        "job_seeker": (ArtisanProfile, "artisan_profile"),
    }

    for user in User.objects.all().iterator():
        entry = pairing.get(user.role)
        if not entry:
            continue  # admin, or unknown role
        ProfileModel, related_name = entry
        # Skip if the paired profile already exists.
        if ProfileModel.objects.filter(user=user).exists():
            continue
        defaults = {}
        if ProfileModel is EmployerProfile:
            defaults["company_name"] = user.full_name or "Unnamed Company"
        ProfileModel.objects.create(user=user, **defaults)


def noop_reverse(apps, schema_editor):
    # Reversing would delete backfilled profiles, which could destroy data the
    # user has since edited. Safer to leave them; reversal is a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_artisanprofile_is_work_verified_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_paired_profiles, noop_reverse),
    ]
