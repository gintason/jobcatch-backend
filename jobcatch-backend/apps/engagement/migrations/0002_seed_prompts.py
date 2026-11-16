"""Default upgrade prompts. Copy, CTAs and cooldowns are editable in admin."""
from django.db import migrations

PROMPTS = [
    dict(key="seeker-pro-popup", audience="job_seeker", trigger="seeker_not_pro", style="modal",
         priority=10, cooldown_hours=72,
         title="Be first to every job — go Pro for ₦3,000",
         body="Get instant WhatsApp alerts the moment a job that matches you is posted.",
         highlights=["Instant WhatsApp job alerts", "Choose your categories & keywords",
                     "Pro badge on your applications"],
         cta_label="Upgrade to Pro", cta_url="/checkout/seeker-pro"),
    dict(key="seeker-pro-banner", audience="job_seeker", trigger="seeker_not_pro", style="banner",
         priority=20, cooldown_hours=24,
         title="Never miss a job",
         body="Pro members get matching jobs on WhatsApp instantly — ₦3,000/month.",
         cta_label="Go Pro", cta_url="/checkout/seeker-pro"),
    dict(key="artisan-verify-popup", audience="artisan", trigger="artisan_not_verified",
         style="modal", priority=10, cooldown_hours=96,
         title="Get the Verified Artisan badge",
         body="Customers choose verified artisans first. Verification is a one-off ₦5,000.",
         highlights=["Blue Verified badge on your profile and listings",
                     "Ranked above unverified artisans", "Reviewed in 1–2 business days"],
         cta_label="Get verified", cta_url="/dashboard/artisan?tab=verification"),
    dict(key="artisan-upgrade-banner", audience="artisan", trigger="artisan_free_tier",
         style="banner", priority=20, cooldown_hours=48,
         title="Get seen first",
         body="Premium and Pro artisans appear at the top of search and on the home page.",
         cta_label="View plans", cta_url="/pricing"),
    dict(key="employer-verify-banner", audience="employer", trigger="employer_not_verified",
         style="banner", priority=10, cooldown_hours=12,
         title="Verify your business to post jobs",
         body="Confirm your contact details, CAC, TIN and director — it takes a few minutes.",
         cta_label="Start verification", cta_url="/dashboard/employer?tab=verification"),
    dict(key="employer-credits-banner", audience="employer", trigger="employer_no_credits",
         style="banner", priority=20, cooldown_hours=24,
         title="Ready to hire?",
         body="Buy a listing package — your job goes live the moment payment is confirmed.",
         cta_label="See packages", cta_url="/checkout/job-package"),
]


def seed(apps, schema_editor):
    UpgradePrompt = apps.get_model("engagement", "UpgradePrompt")
    for spec in PROMPTS:
        spec = dict(spec)
        UpgradePrompt.objects.get_or_create(key=spec.pop("key"), defaults=spec)


class Migration(migrations.Migration):

    dependencies = [("engagement", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
