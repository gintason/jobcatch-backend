"""Starter delivery pricing (editable in Django admin → Delivery rates)."""
from decimal import Decimal

from django.db import migrations

RATES = [
    # vehicle, base, per km, minimum, errand surcharge
    ("bike", "700", "150", "1200", "500"),
    ("tricycle", "900", "180", "1500", "500"),
    ("car", "1500", "250", "2500", "700"),
    ("van", "3000", "400", "5000", "1000"),
]


def seed(apps, schema_editor):
    DeliveryRate = apps.get_model("dispatch", "DeliveryRate")
    for vehicle, base, per_km, minimum, errand in RATES:
        DeliveryRate.objects.get_or_create(vehicle_type=vehicle, defaults={
            "base_fare": Decimal(base), "per_km": Decimal(per_km),
            "minimum_fare": Decimal(minimum), "errand_fee": Decimal(errand)})


class Migration(migrations.Migration):
    dependencies = [("dispatch", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
