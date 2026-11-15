"""Phase 2B tests: booking creation, the status machine, and role-correct actions."""
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import Category, Service

pytestmark = pytest.mark.django_db


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def service():
    artisan = _user("artisan@x.com", "artisan")
    cat = Category.objects.create(name="Plumbing")
    return Service.objects.create(
        artisan=artisan.artisan_profile, category=cat,
        title="Pipe fix", description="d", base_price=5000,
    )


def _make_booking(customer, service, status=BookingStatus.PENDING):
    return Booking.objects.create(
        customer=customer.customer_profile, artisan=service.artisan, service=service,
        scheduled_for=timezone.now() + timedelta(days=1),
        location=Point(7.49, 9.06), address="Abuja", agreed_price=service.base_price,
        status=status,
    )


def test_customer_creates_booking(client, service):
    customer = _user("c@x.com", "customer")
    client.force_authenticate(customer)
    resp = client.post("/api/v1/bookings/", {
        "service": str(service.id),
        "scheduled_for": (timezone.now() + timedelta(days=2)).isoformat(),
        "latitude": 9.06, "longitude": 7.49, "address": "Wuse, Abuja",
    }, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["artisan"] == str(service.artisan.id)      # derived from service
    assert body["agreed_price"] == "5000.00"               # snapshotted price


def test_non_customer_cannot_create_booking(client, service):
    other_artisan = _user("a2@x.com", "artisan")
    client.force_authenticate(other_artisan)
    resp = client.post("/api/v1/bookings/", {
        "service": str(service.id),
        "scheduled_for": (timezone.now() + timedelta(days=2)).isoformat(),
        "latitude": 9.0, "longitude": 7.0, "address": "x",
    }, format="json")
    assert resp.status_code == 403


def test_artisan_accepts_booking_and_history_recorded(client, service):
    customer = _user("c2@x.com", "customer")
    booking = _make_booking(customer, service)
    client.force_authenticate(service.artisan.user)
    resp = client.post(f"/api/v1/bookings/{booking.id}/accept/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    booking.refresh_from_db()
    assert booking.status == BookingStatus.ACCEPTED
    assert booking.history.count() == 1                    # transition audited


def test_illegal_transition_rejected(client, service):
    customer = _user("c3@x.com", "customer")
    booking = _make_booking(customer, service)             # pending
    client.force_authenticate(service.artisan.user)
    resp = client.post(f"/api/v1/bookings/{booking.id}/complete/")  # pending -> completed illegal
    assert resp.status_code == 400


def test_customer_cannot_accept(client, service):
    customer = _user("c4@x.com", "customer")
    booking = _make_booking(customer, service)
    client.force_authenticate(customer)
    resp = client.post(f"/api/v1/bookings/{booking.id}/accept/")
    assert resp.status_code == 403                         # accept is artisan-only


def test_other_artisan_cannot_touch_booking(client, service):
    customer = _user("c5@x.com", "customer")
    booking = _make_booking(customer, service)
    intruder = _user("intruder@x.com", "artisan")
    client.force_authenticate(intruder)
    resp = client.post(f"/api/v1/bookings/{booking.id}/accept/")
    assert resp.status_code == 404                         # not in intruder's scope


def test_full_happy_path_and_cancel(client, service):
    customer = _user("c6@x.com", "customer")
    booking = _make_booking(customer, service)
    artisan = service.artisan.user

    client.force_authenticate(artisan)
    assert client.post(f"/api/v1/bookings/{booking.id}/accept/").json()["status"] == "accepted"
    assert client.post(f"/api/v1/bookings/{booking.id}/start/").json()["status"] == "in_progress"
    assert client.post(f"/api/v1/bookings/{booking.id}/complete/").json()["status"] == "completed"

    # A completed booking can no longer be cancelled.
    assert client.post(f"/api/v1/bookings/{booking.id}/cancel/").status_code == 400


def test_customer_can_cancel_pending(client, service):
    customer = _user("c7@x.com", "customer")
    booking = _make_booking(customer, service)
    client.force_authenticate(customer)
    resp = client.post(f"/api/v1/bookings/{booking.id}/cancel/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_lists_are_scoped_per_role(client, service):
    customer = _user("c8@x.com", "customer")
    _make_booking(customer, service)
    # customer sees their booking
    client.force_authenticate(customer)
    assert client.get("/api/v1/bookings/").json()["count"] == 1
    # artisan sees the same booking from their side
    client.force_authenticate(service.artisan.user)
    assert client.get("/api/v1/bookings/").json()["count"] == 1
    # an unrelated customer sees none
    client.force_authenticate(_user("stranger@x.com", "customer"))
    assert client.get("/api/v1/bookings/").json()["count"] == 0
