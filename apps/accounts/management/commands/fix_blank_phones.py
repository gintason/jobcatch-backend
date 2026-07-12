"""Convert blank ('') phone values to NULL so the unique index stops colliding."""
from django.core.management.base import BaseCommand

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Set phone = NULL wherever it is an empty string."

    def handle(self, *args, **options):
        updated = User.objects.filter(phone="").update(phone=None)
        self.stdout.write(self.style.SUCCESS(f"Fixed {updated} user(s) with a blank phone."))
