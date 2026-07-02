"""
Phase 2D tests: payment initialization, webhook signature verification,
and subscription activation. Gateway network calls are mocked.
"""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import Category, Service
from apps.payments.models import Payment, PaymentPurpose, PaymentStatus
from apps.subscriptions.models import Subscription

pytestmark = pytest.mark.django_db
TEST_SECRET = "sk_test_secret"


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


def _booking(customer, artisan):
    cat = Category.objects.create(name="Plumbing")
    service = Service.objects.create(artisan=artisan.artisan_profile, category=cat,
                                     title="Pipe", description="d", base_price=5000)
    return Booking.objects.create(
        customer=customer.customer_profile, artisan=artisan.artisan_profile,
        service=service, scheduled_for=timezone.now() + timedelta(days=1),
        location=Point(7.49, 9.06), address="Abuja", agreed_price=5000,
        status=BookingStatus.COMPLETED,
    )


@patch("apps.payments.views.get_gateway")
def test_initialize_booking_payment(mock_gateway, client):
    mock_gateway.return_value.initialize.return_value = {
        "authorization_url": "https://paystack.test/pay/abc", "reference": "ref"
    }
    customer = _user("c@x.com", "customer")
    artisan = _user("a@x.com", "artisan")
    booking = _booking(customer, artisan)

    client.force_authenticate(customer)
    resp = client.post("/api/v1/payments/initialize/",
                       {"purpose": "booking", "booking": str(booking.id)}, format="json")
    assert resp.status_code == 201
    assert resp.json()["authorization_url"].startswith("https://paystack.test")

    payment = Payment.objects.get(payer=customer)
    assert payment.amount == 5000
    assert payment.commission == 500          # 10% of 5000
    assert payment.status == PaymentStatus.INITIATED


@patch("apps.payments.views.get_gateway")
def test_initialize_subscription_creates_inactive_sub(mock_gateway, client):
    mock_gateway.return_value.initialize.return_value = {
        "authorization_url": "https://paystack.test/pay/sub", "reference": "ref"
    }
    artisan = _user("a2@x.com", "artisan")
    client.force_authenticate(artisan)
    resp = client.post("/api/v1/payments/initialize/",
                       {"purpose": "subscription", "plan": "premium"}, format="json")
    assert resp.status_code == 201

    sub = Subscription.objects.get(user=artisan)
    assert sub.plan == "premium"
    assert sub.is_active is False               # activates only on webhook success


def test_cannot_pay_for_someone_elses_booking(client):
    owner = _user("owner@x.com", "customer")
    artisan = _user("a3@x.com", "artisan")
    booking = _booking(owner, artisan)
    intruder = _user("intruder@x.com", "customer")
    client.force_authenticate(intruder)
    resp = client.post("/api/v1/payments/initialize/",
                       {"purpose": "booking", "booking": str(booking.id)}, format="json")
    assert resp.status_code == 400


@override_settings(PAYSTACK_SECRET_KEY=TEST_SECRET)
def test_webhook_valid_signature_activates_subscription(client):
    artisan = _user("a4@x.com", "artisan")
    payment = Payment.objects.create(
        payer=artisan, purpose=PaymentPurpose.SUBSCRIPTION, gateway="paystack",
        reference="JC-SUBREF", amount=2500, status=PaymentStatus.INITIATED,
    )
    Subscription.objects.create(user=artisan, plan="premium", is_active=False,
                                started_at=timezone.now(), payment=payment)

    body = json.dumps({"event": "charge.success",
                       "data": {"reference": "JC-SUBREF"}})
    sig = hmac.new(TEST_SECRET.encode(), body.encode(), hashlib.sha512).hexdigest()

    resp = client.post("/api/v1/payments/webhook/paystack/", data=body,
                       content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
    assert resp.status_code == 200

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    sub = Subscription.objects.get(payment=payment)
    assert sub.is_active is True
    artisan.artisan_profile.refresh_from_db()
    assert artisan.artisan_profile.is_featured is True     # premium -> featured


@override_settings(PAYSTACK_SECRET_KEY=TEST_SECRET)
def test_webhook_invalid_signature_rejected(client):
    payment = Payment.objects.create(
        payer=_user("a5@x.com", "artisan"), purpose=PaymentPurpose.BOOKING,
        gateway="paystack", reference="JC-BADSIG", amount=1000,
        status=PaymentStatus.INITIATED,
    )
    body = json.dumps({"event": "charge.success", "data": {"reference": "JC-BADSIG"}})
    resp = client.post("/api/v1/payments/webhook/paystack/", data=body,
                       content_type="application/json",
                       HTTP_X_PAYSTACK_SIGNATURE="deadbeef")
    assert resp.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.INITIATED       # untouched


def test_payment_history_scoped_to_payer(client):
    u1 = _user("u1@x.com", "customer")
    u2 = _user("u2@x.com", "customer")
    Payment.objects.create(payer=u1, purpose=PaymentPurpose.BOOKING, gateway="paystack",
                           reference="R1", amount=100, status=PaymentStatus.SUCCESS)
    Payment.objects.create(payer=u2, purpose=PaymentPurpose.BOOKING, gateway="paystack",
                           reference="R2", amount=200, status=PaymentStatus.SUCCESS)
    client.force_authenticate(u1)
    resp = client.get("/api/v1/payments/")
    assert resp.json()["count"] == 1                       # only own transactions
