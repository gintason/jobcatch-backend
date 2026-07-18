import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CVSubmission",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cv_file", models.FileField(upload_to="cv_service/")),
                ("headline", models.CharField(blank=True, default="", max_length=180)),
                ("note", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("pending", "Pending review"), ("reviewed", "Reviewed"), ("forwarded", "Forwarded to employer(s)")], db_index=True, default="pending", max_length=20)),
                ("seeker", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cv_submissions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="CVServiceAccess",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=False)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("payment_reference", models.CharField(blank=True, default="", max_length=100)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cv_service_access", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "CV service access", "verbose_name_plural": "CV service access"},
        ),
        migrations.CreateModel(
            name="CVReferral",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("admin_note", models.TextField(blank=True, default="", help_text="Optional message shown to the employer alongside the CV.")),
                ("employer", models.ForeignKey(help_text="The employer account this CV was sent to.", on_delete=django.db.models.deletion.CASCADE, related_name="referred_cvs", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referrals", to="cvservice.cvsubmission")),
            ],
            options={"ordering": ("-created_at",), "unique_together": {("submission", "employer")}},
        ),
    ]
