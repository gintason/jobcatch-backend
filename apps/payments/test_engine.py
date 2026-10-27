"""Transaction engine: Flutterwave webhook, receipts, reconciliation, invoices."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.payments.models import Payment, PaymentStatus
from apps.subscriptions.models import Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user("p@x.com", "Str0ng!Pass9", full_name="Payer",
                                    role="artisan", is_email_verified=True)


def _sub_payment(user, gateway="flutterwave"):
    payment = Payment.objects.create(payer=user, purpose="subscription", gateway=gateway,
                                     reference="JC-FLW1", amount=Decimal("2500"),
                                     description="Pro plan")
    from django.utils import timezone

    Subscription.objects.create(user=user, plan="pro", is_active=False,
                                started_at=timezone.now(), payment=payment)
    return payment


def test_invoice_number_assigned(user):
    p = Payment.objects.create(payer=user, purpose="booking", gateway="paystack",
                               reference="R-INV", amount=10)
    assert p.invoice_number.startswith("JCI-")


@override_settings(FLUTTERWAVE_WEBHOOK_HASH="hash123", FLUTTERWAVE_SECRET_KEY="sk")
def test_flutterwave_webhook_reverifies_before_fulfilling(client, user):
    payment = _sub_payment(user)
    body = {"event": "charge.completed",
            "data": {"tx_ref": "JC-FLW1", "status": "successful", "amount": 2500}}
    # Wrong hash -> rejected.
    assert client.post("/api/v1/payments/webhook/flutterwave/", body, format="json",
                       HTTP_VERIF_HASH="wrong").status_code == 400
    # Right hash, but the API says it wasn't paid -> nothing delivered.
    with patch("apps.payments.gateways.FlutterwaveGateway.verify",
               return_value={"status": "pending", "amount": None, "currency": "NGN", "raw": {}}):
        client.post("/api/v1/payments/webhook/flutterwave/", body, format="json",
                    HTTP_VERIF_HASH="hash123")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.INITIATED
    # API confirms -> fulfilled.
    with patch("apps.payments.gateways.FlutterwaveGateway.verify",
               return_value={"status": "success", "amount": Decimal("2500"),
                             "currency": "NGN", "raw": {}}):
        client.post("/api/v1/payments/webhook/flutterwave/", body, format="json",
                    HTTP_VERIF_HASH="hash123")
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS and payment.fulfilled_at
    assert Subscription.objects.get(payment=payment).is_active


def test_receipt_visible_only_to_payer(client, user):
    payment = _sub_payment(user, gateway="paystack")
    client.force_authenticate(user)
    data = client.get(f"/api/v1/payments/receipt/{payment.reference}/").json()
    assert data["invoice_number"] == payment.invoice_number
    assert data["purpose_label"] == "Subscription"
    other = User.objects.create_user("o@x.com", "Str0ng!Pass9", full_name="O", role="customer")
    client.force_authenticate(other)
    assert client.get(f"/api/v1/payments/receipt/{payment.reference}/").status_code == 404


def test_reconcile_verifies_stale_payments(user):
    from datetime import timedelta

    from django.utils import timezone

    from apps.payments.services import reconcile_payments

    payment = _sub_payment(user, gateway="paystack")
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=30))
    with patch("apps.payments.gateways.PaystackGateway.verify",
               return_value={"status": "success", "amount": Decimal("2500"),
                             "currency": "NGN", "raw": {}}):
        reconcile_payments()
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
