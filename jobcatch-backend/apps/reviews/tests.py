"""Phase 2B tests: reviews on completed bookings + artisan rating recomputation."""
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
def artisan():
    return _user("artisan@x.com", "artisan")


@pytest.fixture
def service(artisan):
    cat = Category.objects.create(name="Plumbing")
    return Service.objects.create(artisan=artisan.artisan_profile, category=cat,
                                  title="Pipe", description="d", base_price=5000)


def _completed_booking(customer, service):
    return Booking.objects.create(
        customer=customer.customer_profile, artisan=service.artisan, service=service,
        scheduled_for=timezone.now() + timedelta(days=1),
        location=Point(7.49, 9.06), address="Abuja", agreed_price=5000,
        status=BookingStatus.COMPLETED,
    )


def test_review_recomputes_artisan_rating(client, artisan, service):
    customer = _user("c@x.com", "customer")
    booking = _completed_booking(customer, service)
    client.force_authenticate(customer)
    resp = client.post("/api/v1/reviews/",
                       {"booking": str(booking.id), "rating": 4, "comment": "Great"},
                       format="json")
    assert resp.status_code == 201

    artisan.artisan_profile.refresh_from_db()
    assert float(artisan.artisan_profile.avg_rating) == 4.0
    assert artisan.artisan_profile.rating_count == 1
    assert artisan.artisan_profile.reputation_score == 80    # 4 stars * 20


def test_second_review_averages(client, artisan, service):
    c1 = _user("c1@x.com", "customer")
    c2 = _user("c2@x.com", "customer")
    b1 = _completed_booking(c1, service)
    b2 = _completed_booking(c2, service)

    client.force_authenticate(c1)
    client.post("/api/v1/reviews/", {"booking": str(b1.id), "rating": 5}, format="json")
    client.force_authenticate(c2)
    client.post("/api/v1/reviews/", {"booking": str(b2.id), "rating": 3}, format="json")

    artisan.artisan_profile.refresh_from_db()
    assert float(artisan.artisan_profile.avg_rating) == 4.0   # (5+3)/2
    assert artisan.artisan_profile.rating_count == 2


def test_cannot_review_incomplete_booking(client, service):
    customer = _user("c3@x.com", "customer")
    booking = Booking.objects.create(
        customer=customer.customer_profile, artisan=service.artisan, service=service,
        scheduled_for=timezone.now() + timedelta(days=1),
        location=Point(7.49, 9.06), address="Abuja", agreed_price=5000,
        status=BookingStatus.PENDING,
    )
    client.force_authenticate(customer)
    resp = client.post("/api/v1/reviews/",
                       {"booking": str(booking.id), "rating": 5}, format="json")
    assert resp.status_code == 400


def test_cannot_review_others_booking(client, service):
    owner = _user("owner@x.com", "customer")
    booking = _completed_booking(owner, service)
    intruder = _user("intruder@x.com", "customer")
    client.force_authenticate(intruder)
    resp = client.post("/api/v1/reviews/",
                       {"booking": str(booking.id), "rating": 5}, format="json")
    assert resp.status_code == 400


def test_cannot_review_twice(client, service):
    customer = _user("c4@x.com", "customer")
    booking = _completed_booking(customer, service)
    client.force_authenticate(customer)
    first = client.post("/api/v1/reviews/",
                        {"booking": str(booking.id), "rating": 5}, format="json")
    assert first.status_code == 201
    second = client.post("/api/v1/reviews/",
                         {"booking": str(booking.id), "rating": 1}, format="json")
    assert second.status_code == 400
