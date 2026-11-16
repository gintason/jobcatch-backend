"""
Employers already verified under the old CAC-certificate flow keep their badge
and their ability to post jobs: each gets a VERIFIED KYC case marked as a
manual override, so admins can see it was grandfathered and re-review if needed.
"""
from django.db import migrations
from django.utils import timezone


def grandfather(apps, schema_editor):
    EmployerProfile = apps.get_model("accounts", "EmployerProfile")
    EmployerKYC = apps.get_model("kyc", "EmployerKYC")
    now = timezone.now()
    for profile in EmployerProfile.objects.filter(is_cac_verified=True).select_related("user"):
        EmployerKYC.objects.get_or_create(
            employer=profile,
            defaults={
                "status": "verified",
                "registered_name": profile.company_name or "",
                "cac_number": "".join(c for c in (profile.cac_number or "") if c.isdigit()),
                "contact_email": profile.user.email,
                "reviewed_at": now,
                "is_manual_override": True,
                "admin_note": "Grandfathered: verified under the legacy CAC certificate review.",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("kyc", "0001_initial"),
        ("accounts", "0009_remove_employerprofile_address_and_more"),
    ]

    operations = [migrations.RunPython(grandfather, migrations.RunPython.noop)]
