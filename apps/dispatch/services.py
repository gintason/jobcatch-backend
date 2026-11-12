"""
Dispatch & delivery: pricing, rider onboarding, matching, the delivery lifecycle
and the split payment.

Lifecycle:
    searching --rider accepts--> accepted --rider confirms pickup--> picked_up
    --customer pays in the app (Paystack split)--> in_transit
    --rider enters the recipient's code--> delivered
    searching/accepted can be cancelled by the customer; an unaccepted request
    expires after DISPATCH_REQUEST_TTL_MINUTES.
"""
import logging
import math
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common import audit
from apps.payments.gateways import GatewayError, get_gateway
from apps.payments.models import PaymentPurpose
from apps.payments.services import CheckoutError, start_checkout

from .models import (
    ACTIVE_STATUSES, Delivery, DeliveryKind, DeliveryRate, DeliveryStatus, RiderProfile,
    RiderStatus,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ pricing
def point(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": "Choose the location on the map."}) from exc
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValidationError({"detail": "Choose the location on the map."})
    return Point(lng, lat, srid=4326)


def road_km(a, b):
    """Great-circle distance × road factor, to 0.1 km."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a.y, a.x, b.y, b.x))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    km = Decimal(str(2 * 6371 * math.asin(math.sqrt(h)))) * settings.DISPATCH_ROAD_FACTOR
    return km.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _round50(amount):
    return (Decimal(amount) / 50).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 50


def split_fare(fare):
    rider = (fare * settings.DISPATCH_RIDER_SHARE_PERCENT / 100).quantize(Decimal("0.01"))
    return rider, fare - rider


def quote(*, pickup, dropoff, vehicle_type, kind):
    rate = DeliveryRate.objects.filter(vehicle_type=vehicle_type, is_active=True).first()
    if not rate:
        raise ValidationError({"vehicle_type": "That vehicle type isn't available."})
    km = road_km(pickup, dropoff)
    if km > settings.DISPATCH_MAX_DISTANCE_KM:
        raise ValidationError({"detail": f"Deliveries are limited to "
                                         f"{settings.DISPATCH_MAX_DISTANCE_KM} km for now."})
    fare = max(rate.minimum_fare, rate.base_fare + rate.per_km * km)
    if kind == DeliveryKind.ERRAND:
        fare += rate.errand_fee
    fare = _round50(fare)
    rider, platform = split_fare(fare)
    return {"distance_km": km, "fare": fare, "rider_share": rider, "platform_share": platform,
            "vehicle_type": vehicle_type, "kind": kind}


# ------------------------------------------------------------------ riders
def rider_for(user):
    rider, _ = RiderProfile.objects.get_or_create(user=user, defaults={"phone": user.phone or ""})
    return rider


def _refresh_rider_status(rider):
    """INCOMPLETE until vehicle, phone, ID and payout account are all in; then PENDING review."""
    complete = bool(rider.phone and rider.vehicle_type and rider.id_document and rider.subaccount_code)
    if rider.status == RiderStatus.INCOMPLETE and complete:
        rider.status = RiderStatus.PENDING
        rider.save(update_fields=["status", "updated_at"])
        _notify_admins_new_rider(rider)
    return rider


def update_rider(rider, data, id_document=None):
    for f in ("phone", "vehicle_type", "plate_number", "city", "state"):
        if f in data:
            setattr(rider, f, data[f])
    if id_document is not None:
        rider.id_document = id_document
    if rider.status == RiderStatus.REJECTED:
        rider.status = RiderStatus.INCOMPLETE       # resubmitting after a rejection
    rider.save()
    return _refresh_rider_status(rider)


def banks():
    gw = get_gateway(settings.DISPATCH_GATEWAY)
    if not gw.is_configured():
        raise ValidationError({"detail": "Payouts are not set up yet."})
    try:
        return gw.list_banks()
    except GatewayError as exc:
        raise ValidationError({"detail": "Could not load banks. Try again shortly."}) from exc


def set_payout_account(rider, *, bank_code, account_number, bank_name=""):
    """Verify the account with Paystack and register it as the rider's split subaccount."""
    if not (account_number.isdigit() and len(account_number) == 10):
        raise ValidationError({"account_number": "Enter your 10-digit NUBAN account number."})
    gw = get_gateway(settings.DISPATCH_GATEWAY)
    if not gw.is_configured():
        raise ValidationError({"detail": "Payouts are not set up yet."})
    try:
        name = gw.resolve_account(account_number=account_number, bank_code=bank_code)
    except GatewayError as exc:
        raise ValidationError({"account_number": "We couldn't find that account. Check the "
                                                 "bank and number."}) from exc
    try:
        code = gw.create_subaccount(
            business_name=f"JobCatch Rider {name}"[:100], bank_code=bank_code,
            account_number=account_number,
            percentage_charge=100 - settings.DISPATCH_RIDER_SHARE_PERCENT,
            description=f"JobCatch dispatch rider {rider.user.email}")
    except GatewayError as exc:
        logger.error("Subaccount creation failed for %s: %s", rider.user.email, exc)
        raise ValidationError({"detail": "We couldn't set up your payout account. "
                                         "Please try again later."}) from exc
    rider.bank_code, rider.bank_name = bank_code, bank_name[:100]
    rider.account_number, rider.account_name, rider.subaccount_code = account_number, name[:150], code
    rider.save(update_fields=["bank_code", "bank_name", "account_number", "account_name",
                              "subaccount_code", "updated_at"])
    audit.record(rider, "rider.payout_account_set", actor=rider.user, bank=bank_name or bank_code)
    return _refresh_rider_status(rider)


def review_rider(rider, *, admin, approve, note=""):
    if approve and not rider.subaccount_code:
        raise ValidationError({"detail": "The rider has no verified payout account yet."})
    rider.status = RiderStatus.APPROVED if approve else RiderStatus.REJECTED
    rider.review_note = note[:255]
    rider.reviewed_at, rider.reviewed_by = timezone.now(), admin
    if not approve:
        rider.is_online = False
    rider.save()
    audit.record(rider, f"rider.{'approved' if approve else 'rejected'}", actor=admin, note=note)
    from apps.notifications.services import notify

    notify(rider.user, kind="rider_review",
           title="You're approved to deliver on JobCatch" if approve else "Rider application needs changes",
           body=("Go online in your rider dashboard to start receiving delivery requests."
                 if approve else note or "Please review your details and resubmit."),
           data={"url": "/dashboard/rider"})
    return rider


def set_availability(rider, *, online, lat=None, lng=None):
    if online and not rider.can_work:
        raise ValidationError({"detail": "Your rider account isn't approved yet."})
    rider.is_online = online
    if lat is not None and lng is not None:
        rider.location = point(lat, lng)
        rider.location_updated_at = timezone.now()
    rider.save(update_fields=["is_online", "location", "location_updated_at", "updated_at"])
    return rider


def open_requests(rider):
    """Requests a rider can take: searching, same vehicle, within range of where they are."""
    if not rider.can_work or not rider.location:
        return Delivery.objects.none()
    return (Delivery.objects.filter(status=DeliveryStatus.SEARCHING, vehicle_type=rider.vehicle_type,
                                    pickup_point__dwithin=(rider.location,
                                                           D(km=settings.DISPATCH_MATCH_RADIUS_KM)))
            .exclude(customer=rider.user)
            .annotate(distance_to_pickup=Distance("pickup_point", rider.location))
            .order_by("distance_to_pickup")[:30])


# ------------------------------------------------------------------ deliveries
def create_delivery(*, customer, data):
    pickup = point(data["pickup_lat"], data["pickup_lng"])
    dropoff = point(data["dropoff_lat"], data["dropoff_lng"])
    q = quote(pickup=pickup, dropoff=dropoff, vehicle_type=data["vehicle_type"], kind=data["kind"])
    if data["kind"] == DeliveryKind.ERRAND and not (data.get("errand_details") or "").strip():
        raise ValidationError({"errand_details": "Tell the rider what to buy."})
    delivery = Delivery.objects.create(
        customer=customer, kind=data["kind"], vehicle_type=data["vehicle_type"],
        pickup_address=data["pickup_address"], pickup_point=pickup,
        pickup_contact_name=data.get("pickup_contact_name", ""),
        pickup_contact_phone=data.get("pickup_contact_phone", ""),
        dropoff_address=data["dropoff_address"], dropoff_point=dropoff,
        recipient_name=data["recipient_name"], recipient_phone=data["recipient_phone"],
        item_description=data["item_description"],
        errand_details=data.get("errand_details", ""), errand_budget=data.get("errand_budget"),
        notes=data.get("notes", ""), distance_km=q["distance_km"], fare=q["fare"],
        rider_share=q["rider_share"], platform_share=q["platform_share"],
        delivery_code=f"{secrets.randbelow(10000):04d}")
    audit.record(delivery, "delivery.created", actor=customer)
    transaction.on_commit(lambda: _alert_nearby_riders(delivery))
    return delivery


def _locked(delivery):
    return Delivery.objects.select_for_update().get(pk=delivery.pk)


def accept(delivery, rider):
    if not rider.can_work:
        raise ValidationError({"detail": "Your rider account isn't approved yet."})
    with transaction.atomic():
        if Delivery.objects.filter(rider=rider, status__in=ACTIVE_STATUSES).exists():
            raise ValidationError({"detail": "Finish your current delivery first."})
        d = _locked(delivery)
        if d.status != DeliveryStatus.SEARCHING:
            raise ValidationError({"detail": "Another rider has already taken this request."})
        if d.vehicle_type != rider.vehicle_type or d.customer_id == rider.user_id:
            raise ValidationError({"detail": "This request isn't available to you."})
        d.rider, d.status, d.accepted_at = rider, DeliveryStatus.ACCEPTED, timezone.now()
        d.save(update_fields=["rider", "status", "accepted_at", "updated_at"])
    audit.record(d, "delivery.accepted", actor=rider.user)
    _notify(d.customer, d, "Rider on the way",
            f"{rider.user.full_name} ({rider.get_vehicle_type_display()}"
            f"{f', {rider.plate_number}' if rider.plate_number else ''}) is heading to the pickup.")
    return d


def release(delivery, rider):
    """Rider gives a request back before pickup; it goes back to other riders."""
    with transaction.atomic():
        d = _locked(delivery)
        if d.rider_id != rider.id or d.status != DeliveryStatus.ACCEPTED:
            raise ValidationError({"detail": "You can only release a request before pickup."})
        d.rider, d.status, d.accepted_at = None, DeliveryStatus.SEARCHING, None
        d.save(update_fields=["rider", "status", "accepted_at", "updated_at"])
    audit.record(d, "delivery.released", actor=rider.user)
    _notify(d.customer, d, "Finding another rider", "Your rider couldn't make it — we're finding another.")
    transaction.on_commit(lambda: _alert_nearby_riders(d))
    return d


def mark_picked_up(delivery, rider):
    with transaction.atomic():
        d = _locked(delivery)
        if d.rider_id != rider.id or d.status != DeliveryStatus.ACCEPTED:
            raise ValidationError({"detail": "This delivery isn't waiting for pickup."})
        d.status, d.picked_up_at = DeliveryStatus.PICKED_UP, timezone.now()
        d.save(update_fields=["status", "picked_up_at", "updated_at"])
    audit.record(d, "delivery.picked_up", actor=rider.user)
    _notify(d.customer, d, f"Pay ₦{d.fare:,.0f} to start your delivery",
            "Your rider has the item. Pay the delivery fee in the app and they'll set off.")
    return d


def start_payment(delivery, customer):
    """Customer pays the fee; Paystack sends the rider's share straight to the rider's bank."""
    if delivery.customer_id != customer.id:
        raise ValidationError({"detail": "Not your delivery."})
    if delivery.status != DeliveryStatus.PICKED_UP:
        raise ValidationError({"detail": "You can pay once the rider has picked up."})
    if not delivery.rider or not delivery.rider.subaccount_code:
        raise ValidationError({"detail": "This rider can't receive payments yet."})

    def link(payment):
        Delivery.objects.filter(pk=delivery.pk).update(payment=payment)

    try:
        _payment, url = start_checkout(
            user=customer, purpose=PaymentPurpose.DELIVERY, amount=delivery.fare,
            description=f"Delivery {delivery.reference}", gateway=settings.DISPATCH_GATEWAY,
            metadata={"delivery_id": str(delivery.id)}, before_gateway=link,
            commission=delivery.platform_share,
            split={"subaccount": delivery.rider.subaccount_code,
                   "platform_amount": delivery.platform_share, "bearer": "account"})
    except CheckoutError as exc:
        raise ValidationError({"detail": str(exc)}) from exc
    return url


def fulfill_delivery_payment(payment):
    """Called once when the delivery fee payment is confirmed."""
    d = Delivery.objects.select_for_update().filter(
        pk=(payment.metadata or {}).get("delivery_id"), payment=payment).first()
    if not d:
        logger.error("Delivery payment %s has no delivery", payment.reference)
        return
    if d.status != DeliveryStatus.PICKED_UP:
        return
    d.status, d.paid_at = DeliveryStatus.IN_TRANSIT, timezone.now()
    d.save(update_fields=["status", "paid_at", "updated_at"])
    audit.record(d, "delivery.paid", reference=payment.reference)
    rider_user = d.rider.user
    transaction.on_commit(lambda: _notify(
        rider_user, d, "Payment received — go!",
        f"The customer paid ₦{d.fare:,.0f}. Your ₦{d.rider_share:,.0f} settles to your bank. "
        f"Deliver to {d.recipient_name}."))


def deliver(delivery, rider, code):
    with transaction.atomic():
        d = _locked(delivery)
        if d.rider_id != rider.id or d.status != DeliveryStatus.IN_TRANSIT:
            raise ValidationError({"detail": "This delivery isn't on the way."})
        if str(code).strip() != d.delivery_code:
            raise ValidationError({"code": "That code is wrong. Ask the recipient for their 4-digit code."})
        d.status, d.delivered_at = DeliveryStatus.DELIVERED, timezone.now()
        d.save(update_fields=["status", "delivered_at", "updated_at"])
        RiderProfile.objects.filter(pk=rider.pk).update(completed_count=F("completed_count") + 1)
    audit.record(d, "delivery.delivered", actor=rider.user)
    _notify(d.customer, d, "Delivered ✓", f"Your delivery {d.reference} was completed. Rate your rider.")
    return d


def cancel(delivery, customer, reason=""):
    with transaction.atomic():
        d = _locked(delivery)
        if d.customer_id != customer.id:
            raise ValidationError({"detail": "Not your delivery."})
        if d.status not in (DeliveryStatus.SEARCHING, DeliveryStatus.ACCEPTED):
            raise ValidationError({"detail": "This delivery can't be cancelled after pickup."})
        rider = d.rider
        d.status, d.cancelled_at, d.cancel_reason = DeliveryStatus.CANCELLED, timezone.now(), reason[:255]
        d.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
    audit.record(d, "delivery.cancelled", actor=customer, reason=reason)
    if rider:
        _notify(rider.user, d, "Delivery cancelled", "The customer cancelled this delivery.")
    return d


def rate(delivery, customer, stars, review=""):
    if delivery.customer_id != customer.id or delivery.status != DeliveryStatus.DELIVERED:
        raise ValidationError({"detail": "You can rate a delivery once it's completed."})
    if delivery.rating:
        raise ValidationError({"detail": "You've already rated this delivery."})
    if not (1 <= int(stars) <= 5):
        raise ValidationError({"rating": "Choose 1 to 5 stars."})
    delivery.rating, delivery.review = int(stars), review[:500]
    delivery.save(update_fields=["rating", "review", "updated_at"])
    RiderProfile.objects.filter(pk=delivery.rider_id).update(
        rating_sum=F("rating_sum") + int(stars), rating_count=F("rating_count") + 1)
    return delivery


def expire_stale_requests():
    cutoff = timezone.now() - timedelta(minutes=settings.DISPATCH_REQUEST_TTL_MINUTES)
    stale = list(Delivery.objects.filter(status=DeliveryStatus.SEARCHING, created_at__lt=cutoff))
    for d in stale:
        Delivery.objects.filter(pk=d.pk, status=DeliveryStatus.SEARCHING).update(
            status=DeliveryStatus.EXPIRED, updated_at=timezone.now())
        _notify(d.customer, d, "No rider available",
                "We couldn't find a rider for your request. Please try again in a few minutes.")
    return len(stale)


# ------------------------------------------------------------------ notifications
def _notify(user, delivery, title, body):
    from apps.notifications.services import notify

    try:
        notify(user, kind="delivery", title=title, body=body,
               data={"delivery_id": str(delivery.id), "url": f"/dispatch/{delivery.id}"})
    except Exception:  # noqa: BLE001 — a notification failure must not break the flow
        logger.exception("Delivery notification failed")


def _alert_nearby_riders(delivery):
    from apps.notifications.models import Notification

    riders = (RiderProfile.objects.filter(
        is_online=True, status=RiderStatus.APPROVED, vehicle_type=delivery.vehicle_type,
        location__dwithin=(delivery.pickup_point, D(km=settings.DISPATCH_MATCH_RADIUS_KM)))
        .exclude(user=delivery.customer).select_related("user")[:25])
    Notification.objects.bulk_create([
        Notification(user=r.user, kind="delivery_request",
                     title=f"New {delivery.get_kind_display().lower()} · ₦{delivery.rider_share:,.0f} for you",
                     body=f"{delivery.pickup_address[:80]} → {delivery.dropoff_address[:80]} "
                          f"({delivery.distance_km} km)",
                     data={"delivery_id": str(delivery.id), "url": "/dashboard/rider"})
        for r in riders])


def _notify_admins_new_rider(rider):
    from apps.accounts.models import User, UserRole
    from apps.notifications.models import Notification

    Notification.objects.bulk_create([
        Notification(user=a, kind="admin_review", title="New rider to review",
                     body=f"{rider.user.full_name} · {rider.get_vehicle_type_display()}",
                     data={"url": "/admin/reviews?queue=riders"})
        for a in User.objects.filter(role=UserRole.ADMIN, is_staff=True, is_active=True)])
