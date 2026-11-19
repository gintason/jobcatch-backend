"""Employer verification fee: pay once at submission; the review starts when it's paid."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.kyc.models import EmployerKYC
from apps.kyc.tests import _complete, admin_user, client, employer, private_media  # noqa: F401
from apps.payments.models import Payment
from apps.payments.services import confirm_payment

pytestmark = pytest.mark.django_db

CHECKOUT = {"authorization_url": "https://checkout.paystack.com/abc"}


@pytest.fixture(autouse=True)
def fee(settings, private_media):  # noqa: F811 - runs after the shared fixture sets the fee to 0
    settings.EMPLOYER_VERIFICATION_FEE = Decimal("8000")
    settings.DEFAULT_PAYMENT_GATEWAY = "paystack"


def _submit(client):  # noqa: F811
    with patch("apps.payments.gateways.PaystackGateway.initialize", return_value=CHECKOUT):
        return client.post("/api/v1/kyc/employer/submit/")


def test_details_show_the_fee(client, employer):  # noqa: F811
    client.force_authenticate(employer)
    data = client.get("/api/v1/kyc/employer/").json()
    assert data["fee"] == "8000" and data["fee_required"] is True and data["fee_paid"] is False


def test_submit_starts_checkout_then_payment_sends_for_review(client, employer):  # noqa: F811
    _complete(client, employer)
    resp = _submit(client)
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["authorization_url"] == CHECKOUT["authorization_url"]
    assert body["status"] == "in_progress"            # not under review until paid

    payment = Payment.objects.get(reference=body["reference"])
    assert payment.purpose == "employer_verify" and payment.amount == Decimal("8000.00")

    confirm_payment(payment.reference, source="test", paid_amount=Decimal("8000"))
    kyc = EmployerKYC.objects.get(employer=employer.employer_profile)
    assert kyc.fee_paid and kyc.status == "pending" and kyc.submitted_at

    # Idempotent: a second confirmation changes nothing.
    confirm_payment(payment.reference, source="test", paid_amount=Decimal("8000"))
    assert EmployerKYC.objects.get(pk=kyc.pk).fee_paid_at == kyc.fee_paid_at


def test_resubmitting_after_rejection_is_free(client, employer, admin_user):  # noqa: F811
    _complete(client, employer)
    kyc = EmployerKYC.objects.get(employer=employer.employer_profile)
    kyc.fee_paid_at = timezone.now()
    kyc.status = "rejected"
    kyc.save()
    resp = _submit(client)
    assert resp.status_code == 200
    assert resp.json()["authorization_url"] is None
    assert resp.json()["status"] == "pending"


def test_incomplete_application_cannot_pay(client, employer):  # noqa: F811
    client.force_authenticate(employer)
    assert _submit(client).status_code == 400
    assert not Payment.objects.exists()


def test_fee_can_be_switched_off(client, employer, settings):  # noqa: F811
    settings.EMPLOYER_VERIFICATION_FEE = Decimal("0")
    _complete(client, employer)
    resp = _submit(client)
    assert resp.json()["authorization_url"] is None and resp.json()["status"] == "pending"
