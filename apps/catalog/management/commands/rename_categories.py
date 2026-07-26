"""
Rename existing home-service categories in place (client request).

Renaming in place — rather than delete + recreate — preserves every Service
already attached to these categories, so no artisan loses a listing.

Matches each category by its OLD slug (the DB unique key), then updates both
the display name and the slug to the new values. Idempotent: once renamed, a
re-run simply finds nothing to do.

    python manage.py rename_categories          # dry run (shows what would change)
    python manage.py rename_categories --yes     # apply
"""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.catalog.models import Category, CategoryKind

# (old name, new name) — slugs are derived, matching the seeder's logic.
RENAMES = [
    ("Driver", "Floor Tiler"),
    ("Mason/Tiler", "Mason/Bricklayer"),
    ("Furniture Installer", "Furniture Maker"),
    ("Security Guard", "Baker/Confectionery"),
    ("Intellectual Property Protection and Registration", "Leather Bag and Shoe Maker"),
]


class Command(BaseCommand):
    help = "Rename home-service categories in place (preserves attached services)."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Apply the changes (otherwise dry run).")

    def handle(self, *args, **options):
        apply = options["yes"]
        changed = 0

        for old_name, new_name in RENAMES:
            old_slug = slugify(old_name)
            new_slug = slugify(new_name)

            cat = Category.objects.filter(
                kind=CategoryKind.HOME_SERVICE, slug=old_slug
            ).first()

            if cat is None:
                # Already renamed? Report and skip.
                if Category.objects.filter(kind=CategoryKind.HOME_SERVICE, slug=new_slug).exists():
                    self.stdout.write(f"  = '{new_name}' already present — skipping")
                else:
                    self.stdout.write(self.style.WARNING(f"  ? '{old_name}' not found — skipping"))
                continue

            # Guard against colliding with an existing different row.
            clash = (Category.objects
                     .filter(kind=CategoryKind.HOME_SERVICE, slug=new_slug)
                     .exclude(pk=cat.pk).exists())
            if clash:
                self.stdout.write(self.style.ERROR(
                    f"  ! '{new_name}' ({new_slug}) already exists as another row — skipping '{old_name}'"
                ))
                continue

            self.stdout.write(f"  '{old_name}' -> '{new_name}'  ({old_slug} -> {new_slug})")
            if apply:
                cat.name = new_name
                cat.slug = new_slug
                cat.save(update_fields=["name", "slug"])
            changed += 1

        if apply:
            self.stdout.write(self.style.SUCCESS(f"\nDone. {changed} categor(y/ies) renamed."))
        else:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — nothing changed. {changed} would be renamed. Re-run with --yes."
            ))
