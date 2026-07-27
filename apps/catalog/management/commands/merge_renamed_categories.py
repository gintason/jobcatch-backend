"""
Merge duplicate categories created during the rename.

Context: the rename updated the seed list, and a subsequent `seed_categories`
run CREATED the new names as fresh rows instead of renaming — so the database
now holds BOTH the old and new categories. This command merges each old
category into its new counterpart:

  1. Reassign every Service on the OLD category to the NEW category
  2. Delete the now-empty OLD category

Idempotent and safe to re-run. Matches by slug (the DB unique key).

    python manage.py merge_renamed_categories          # dry run
    python manage.py merge_renamed_categories --yes      # apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.catalog.models import Category, CategoryKind, Service

# (old name, new name)
PAIRS = [
    ("Driver", "Floor Tiler"),
    ("Mason/Tiler", "Mason/Bricklayer"),
    ("Furniture Installer", "Furniture Maker"),
    ("Security Guard", "Baker/Confectionery"),
    ("Intellectual Property Protection and Registration", "Leather Bag and Shoe Maker"),
]


class Command(BaseCommand):
    help = "Merge old categories into their renamed counterparts, preserving services."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Apply (otherwise dry run).")

    def handle(self, *args, **options):
        apply = options["yes"]
        merged = 0

        for old_name, new_name in PAIRS:
            old_slug, new_slug = slugify(old_name), slugify(new_name)

            old = Category.objects.filter(kind=CategoryKind.HOME_SERVICE, slug=old_slug).first()
            new = Category.objects.filter(kind=CategoryKind.HOME_SERVICE, slug=new_slug).first()

            if old is None:
                self.stdout.write(f"  = '{old_name}' already gone — nothing to merge")
                continue
            if new is None:
                self.stdout.write(self.style.WARNING(
                    f"  ? '{new_name}' target missing — skipping '{old_name}'"
                ))
                continue

            svc_count = Service.objects.filter(category=old).count()
            self.stdout.write(
                f"  '{old_name}' -> '{new_name}': move {svc_count} service(s), then delete old"
            )

            if apply:
                with transaction.atomic():
                    Service.objects.filter(category=old).update(category=new)
                    old.delete()
            merged += 1

        if apply:
            self.stdout.write(self.style.SUCCESS(f"\nDone. {merged} category merge(s) completed."))
        else:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — nothing changed. {merged} merge(s) pending. Re-run with --yes."
            ))
