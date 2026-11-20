"""
Dispatch: package delivery and food / essentials pickup.

RiderProfile     a rider's vehicle, ID document, payout bank account (a Paystack
                 subaccount) and live location. Admin-approved before they can work.
DeliveryRate     per-vehicle pricing set by JobCatch: base + per-km, with a minimum.
Delivery         one job: a package from A to B, or a food & essentials pickup —
                 the customer has already ordered AND paid the vendor; the rider
                 only collects the order and delivers it (riders never buy
                 anything for customers). The customer pays the delivery fee in
                 the app once the rider confirms pickup; Paystack splits it —
                 the rider's share settles straight to their bank, JobCatch's to
                 ours — so JobCatch never holds the rider's money.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.db import models as gis
from django.db import models

from apps.common.models import BaseModel
from apps.common.storage import private_storage


class VehicleType(models.TextChoices):
    BIKE = "bike", "Motorbike"
    TRICYCLE = "tricycle", "Tricycle (keke)"
    CAR = "car", "Car"
    VAN = "van", "Van"


class RiderStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    PENDING = "pending", "Under review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SUSPENDED = "suspended", "Suspended"


def rider_document_upload_to(instance, filename):
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"rider-documents/{instance.user_id}/{uuid.uuid4().hex}{ext}"


class RiderIdType(models.TextChoices):
    NIN = "nin", "NIN slip / National ID card"
    DRIVERS_LICENCE = "drivers_licence", "Driver's licence"
    VOTERS_CARD = "voters_card", "Voter's card"
    PASSPORT = "passport", "International passport"


def rider_selfie_upload_to(instance, filename):
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10] or ".jpg"
    return f"rider-selfies/{instance.user_id}/{uuid.uuid4().hex}{ext}"


class RiderProfile(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="rider_profile")
    phone = models.CharField(max_length=20, blank=True)
    vehicle_type = models.CharField(max_length=10, choices=VehicleType.choices,
                                    default=VehicleType.BIKE)
    plate_number = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=60, blank=True)
    # Identity: what the document is, its number, a copy of it, and a live selfie
    # that an admin compares with the photo on the ID before approving.
    id_type = models.CharField(max_length=20, choices=RiderIdType.choices, blank=True)
    id_number = models.CharField(max_length=30, blank=True)
    id_document = models.FileField(storage=private_storage, upload_to=rider_document_upload_to,
                                   null=True, blank=True, max_length=255)
    selfie = models.FileField(storage=private_storage, upload_to=rider_selfie_upload_to,
                              null=True, blank=True, max_length=255)
    status = models.CharField(max_length=12, choices=RiderStatus.choices,
                              default=RiderStatus.INCOMPLETE, db_index=True)
    review_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    # Payout account (verified with Paystack; registered as a split subaccount)
    bank_code = models.CharField(max_length=20, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=10, blank=True)
    account_name = models.CharField(max_length=150, blank=True)
    subaccount_code = models.CharField(max_length=40, blank=True)
    # Availability
    is_online = models.BooleanField(default=False, db_index=True)
    location = gis.PointField(geography=True, null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    completed_count = models.PositiveIntegerField(default=0)
    rating_sum = models.PositiveIntegerField(default=0)
    rating_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Rider<{self.user.email}> {self.vehicle_type} · {self.status}"

    @property
    def rating(self):
        return round(self.rating_sum / self.rating_count, 1) if self.rating_count else None

    @property
    def can_work(self):
        return self.status == RiderStatus.APPROVED and bool(self.subaccount_code)


class DeliveryRate(BaseModel):
    vehicle_type = models.CharField(max_length=10, choices=VehicleType.choices, unique=True)
    base_fare = models.DecimalField(max_digits=10, decimal_places=2)
    per_km = models.DecimalField(max_digits=10, decimal_places=2)
    minimum_fare = models.DecimalField(max_digits=10, decimal_places=2)
    errand_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("500"))
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ("base_fare",)

    def __str__(self):
        return f"{self.get_vehicle_type_display()}: ₦{self.base_fare} + ₦{self.per_km}/km"


class DeliveryKind(models.TextChoices):
    PACKAGE = "package", "Send a package"
    # Stored as "errand" for compatibility; it is a pickup of an already-paid vendor order.
    ERRAND = "errand", "Food & essentials pickup"


class DeliveryStatus(models.TextChoices):
    SEARCHING = "searching", "Finding a rider"
    ACCEPTED = "accepted", "Rider on the way to pickup"
    PICKED_UP = "picked_up", "Picked up — awaiting payment"
    IN_TRANSIT = "in_transit", "On the way"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "No rider found"


ACTIVE_STATUSES = (DeliveryStatus.SEARCHING, DeliveryStatus.ACCEPTED,
                   DeliveryStatus.PICKED_UP, DeliveryStatus.IN_TRANSIT)


class Delivery(BaseModel):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="deliveries")
    rider = models.ForeignKey(RiderProfile, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="deliveries")
    kind = models.CharField(max_length=10, choices=DeliveryKind.choices,
                            default=DeliveryKind.PACKAGE)
    vehicle_type = models.CharField(max_length=10, choices=VehicleType.choices,
                                    default=VehicleType.BIKE)
    status = models.CharField(max_length=12, choices=DeliveryStatus.choices,
                              default=DeliveryStatus.SEARCHING, db_index=True)

    pickup_address = models.CharField(max_length=255)
    pickup_point = gis.PointField(geography=True)
    pickup_contact_name = models.CharField(max_length=100, blank=True)
    pickup_contact_phone = models.CharField(max_length=20, blank=True)
    dropoff_address = models.CharField(max_length=255)
    dropoff_point = gis.PointField(geography=True)
    recipient_name = models.CharField(max_length=100)
    recipient_phone = models.CharField(max_length=20)

    item_description = models.CharField(max_length=255)
    # Errands: what to buy and roughly what it costs. The items are paid to the
    # rider directly on delivery (cash/transfer) — only the delivery fee goes
    # through JobCatch.
    errand_details = models.TextField(blank=True)
    errand_budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)

    distance_km = models.DecimalField(max_digits=7, decimal_places=2)
    fare = models.DecimalField(max_digits=10, decimal_places=2)
    rider_share = models.DecimalField(max_digits=10, decimal_places=2)
    platform_share = models.DecimalField(max_digits=10, decimal_places=2)
    # Given to the recipient; the rider enters it to complete the delivery.
    delivery_code = models.CharField(max_length=4)

    payment = models.ForeignKey("payments.Payment", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="deliveries")
    accepted_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    review = models.CharField(max_length=500, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["status", "vehicle_type"]),
                   models.Index(fields=["customer", "status"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.pickup_address[:30]} → {self.dropoff_address[:30]} · {self.status}"

    @property
    def reference(self):
        return f"JCD-{self.created_at:%Y%m%d}-{str(self.id)[:8].upper()}"
