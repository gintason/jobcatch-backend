"""
Add employer contact details to EmployerProfile.

The employer dashboard now carries a full company profile: company name (already
present), address, contact email, website (already present) and telephone.
These are shown to job seekers so they know who they are applying to.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_backfill_all_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="employerprofile",
            name="address",
            field=models.CharField(max_length=255, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="contact_email",
            field=models.EmailField(max_length=254, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="phone",
            field=models.CharField(max_length=32, blank=True, default=""),
        ),
    ]
