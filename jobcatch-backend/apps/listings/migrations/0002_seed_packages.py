"""
Starter job-listing packages. Prices and terms are editable in Django admin
(Listings › Job packages) — these are only sensible defaults.
"""
from decimal import Decimal

from django.db import migrations

PACKAGES = [
    dict(slug="single-post", name="Single Post", sort_order=10, price=Decimal("5000"),
         job_credits=1, listing_days=30, validity_days=90, is_featured=False,
         description="One job listing, live for 30 days.",
         features=["1 job listing", "Live for 30 days", "Verified Employer badge shown"]),
    dict(slug="featured-post", name="Featured Post", sort_order=20, price=Decimal("10000"),
         job_credits=1, listing_days=30, validity_days=90, is_featured=True,
         highlight_label="Most visible",
         description="Pinned above standard listings and shown on the home page.",
         features=["1 featured job listing", "Pinned to the top of job search",
                   "Shown in Featured Jobs on the home page", "Live for 30 days"]),
    dict(slug="bulk-5", name="Bulk 5", sort_order=30, price=Decimal("20000"),
         job_credits=5, listing_days=30, validity_days=180, is_featured=False,
         highlight_label="Save 20%",
         description="Five job listings to use within 6 months.",
         features=["5 job listings", "Each live for 30 days", "Use within 6 months"]),
    dict(slug="bulk-10", name="Bulk 10", sort_order=40, price=Decimal("35000"),
         job_credits=10, listing_days=30, validity_days=365, is_featured=False,
         highlight_label="Best value",
         description="Ten job listings to use within a year.",
         features=["10 job listings", "Each live for 30 days", "Use within 12 months"]),
    # Off by default — switch on in admin to offer every employer one free post.
    dict(slug="first-post-free", name="First Post Free", sort_order=0, price=Decimal("0"),
         job_credits=1, listing_days=14, validity_days=30, is_featured=False,
         limit_per_employer=1, is_active=False,
         description="Try JobCatch: your first listing is on us (14 days).",
         features=["1 job listing", "Live for 14 days", "Once per employer"]),
]


def seed(apps, schema_editor):
    JobPackage = apps.get_model("listings", "JobPackage")
    for spec in PACKAGES:
        spec = dict(spec)
        slug = spec.pop("slug")
        JobPackage.objects.get_or_create(slug=slug, defaults=spec)


class Migration(migrations.Migration):

    dependencies = [("listings", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
