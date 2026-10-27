"""Wallet + bill payments via VTU.ng (HTTP layer mocked)."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillOrder, BillStatus, Wallet, WalletTransaction
from apps.billing.services import _post, credit_wallet_from_payment, requery_stale_orders
from apps.billing.vtu import VTUClient
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import confirm_payment

pytestmark = pytest.mark.django_db

VTU = dict(VTU_USERNAME="reseller", VTU_PASSWORD="pw", VTU_USER_PIN="1234")


@pytest.fixture(autouse=True)
def _vtu_settings(settings):
    for k, v in VTU.items():
        setattr(settings, k, v)
    cache.clear()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user("buyer@x.com", "Str0ng!Pass9", full_name="Buyer",
                                    role="customer", is_email_verified=True)


def _fund(user, amount):
    from apps.billing.models import TxnKind, TxnSource

    _post(user, kind=TxnKind.CREDIT, source=TxnSource.ADJUSTMENT, amount=amount,
          reference=f"seed:{amount}", description="test")


class FakeVTU:
    """Stands in for VTUClient._raw: scripted responses per path."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, path, payload=None, token=None, timeout=45):
        self.calls.append((path, payload))
        if path == "/jwt-auth/v1/token":
            return 200, {"token": "jwt"}
        result = self.responses.get(path.split("?")[0])
        if isinstance(result, Exception):
            raise result
        return result if result else (404, {"code": "rest_no_route"})


def _order(status, **extra):
    return 200, {"code": "success", "message": "ORDER",
                 "data": {"order_id": 77, "status": status, **extra}}


def _buy_airtime(client, user, amount="500", method="wallet"):
    client.force_authenticate(user)
    return client.post("/api/v1/billing/orders/", {
        "service": "airtime", "provider": "mtn", "customer_id": "0803 123 4567",
        "amount": amount, "payment_method": method}, format="json")


def test_wallet_airtime_success(client, user):
    _fund(user, 1000)
    fake = FakeVTU({"/api/v2/airtime": _order("completed-api")})
    with patch.object(VTUClient, "_raw", fake):
        resp = _buy_airtime(client, user)
    assert resp.status_code == 201, resp.json()
    assert resp.json()["status"] == "successful"
    assert fake.calls[-1][1]["phone"] == "08031234567"
    assert Wallet.objects.get(user=user).balance == Decimal("500.00")


def test_insufficient_wallet_balance(client, user):
    _fund(user, 100)
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        resp = _buy_airtime(client, user)
    assert resp.status_code == 400
    assert BillOrder.objects.count() == 0            # rolled back with the failed debit


def test_definite_failure_refunds_wallet(client, user):
    _fund(user, 1000)
    fake = FakeVTU({"/api/v2/airtime": (400, {"code": "invalid_service",
                                               "message": "Phone does not match network"})})
    with patch.object(VTUClient, "_raw", fake):
        resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "refunded"
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")
    assert WalletTransaction.objects.filter(source="refund").count() == 1


def test_unknown_outcome_waits_then_requery_completes(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"/api/v2/airtime": (502, {})})):
        resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "processing"      # NOT refunded: order may exist
    assert Wallet.objects.get(user=user).balance == Decimal("500.00")

    BillOrder.objects.update(updated_at="2000-01-01T00:00:00Z")
    with patch.object(VTUClient, "_raw", FakeVTU({"/api/v2/requery": _order("completed-api")})):
        assert requery_stale_orders() == 1
    assert BillOrder.objects.get().status == BillStatus.SUCCESSFUL


def test_biller_refund_via_webhook_is_signed_and_idempotent(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"/api/v2/airtime": _order("processing-api")})):
        order_id = _buy_airtime(client, user).json()["id"]
    order = BillOrder.objects.get(pk=order_id)
    payload = {"order_id": 77, "status": "refunded", "request_id": order.request_id}
    body = json.dumps(payload, separators=(",", ":"))
    sig = hmac.new(b"1234", body.encode(), hashlib.sha256).hexdigest()

    bad = client.post("/api/v1/billing/webhook/vtu/", data=body, content_type="application/json",
                      HTTP_X_SIGNATURE="nope")
    assert bad.status_code == 403
    for _ in range(2):   # delivered twice
        ok = client.post("/api/v1/billing/webhook/vtu/", data=body,
                         content_type="application/json", HTTP_X_SIGNATURE=sig)
        assert ok.status_code == 200
    order.refresh_from_db()
    assert order.status == BillStatus.REFUNDED
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")   # refunded once


def test_data_price_comes_from_biller_not_client(client, user):
    _fund(user, 5000)
    fake = FakeVTU({
        "/api/v2/variations/data": (200, {"code": "success", "data": [
            {"variation_id": 2682, "service_id": "mtn", "data_plan": "1GB - 30 Days",
             "price": "799", "availability": "Available"}]}),
        "/api/v2/data": _order("completed-api"),
    })
    client.force_authenticate(user)
    with patch.object(VTUClient, "_raw", fake):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "data", "provider": "mtn", "customer_id": "08031234567",
            "variation_id": "2682", "amount": "1", "payment_method": "wallet"}, format="json")
    assert resp.status_code == 201, resp.json()
    assert resp.json()["amount"] == "799.00"
    assert resp.json()["variation_label"] == "1GB - 30 Days"


@patch("apps.payments.services.get_gateway")
def test_card_payment_dispatches_after_confirmation(mock_gw, client, user,
                                                    django_capture_on_commit_callbacks):
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://pay/x"}
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        resp = _buy_airtime(client, user, method="card")
    assert resp.json()["status"] == "awaiting_payment"
    assert resp.json()["authorization_url"] == "https://pay/x"
    payment = Payment.objects.get(purpose="bill_payment")

    fake = FakeVTU({"/api/v2/airtime": _order("completed-api")})
    with patch.object(VTUClient, "_raw", fake), \
            django_capture_on_commit_callbacks(execute=True):
        confirm_payment(payment.reference, source="test", paid_amount=payment.amount)
    assert BillOrder.objects.get().status == BillStatus.SUCCESSFUL
    assert Payment.objects.get(pk=payment.pk).status == PaymentStatus.SUCCESS


def test_topup_credit_is_idempotent(user):
    payment = Payment.objects.create(payer=user, purpose="wallet_topup", gateway="paystack",
                                     reference="JC-TOPUP1", amount=2500)
    credit_wallet_from_payment(payment)
    credit_wallet_from_payment(payment)
    assert Wallet.objects.get(user=user).balance == Decimal("2500.00")


def test_airtime_limits(client, user):
    _fund(user, 100000)
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        assert _buy_airtime(client, user, amount="60000").status_code == 400
        assert _buy_airtime(client, user, amount="20.5").status_code == 400


@override_settings(VTU_USERNAME="", VTU_PASSWORD="")
def test_unconfigured_biller_refunds(client, user):
    _fund(user, 1000)
    resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "refunded"
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")
