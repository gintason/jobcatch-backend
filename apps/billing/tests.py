"""Wallet + bill payments via the VTU.ng Partner API (HTTP layer mocked)."""
import base64
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
from apps.billing.vtu import VTUClient, VTUUnknown
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import confirm_payment

pytestmark = pytest.mark.django_db

LIVE = dict(VTU_API_KEY="vtu_sk_live_test", VTU_BASE_URL="https://api.vtu.ng/v1",
            VTU_WEBHOOK_TOKEN="hooktoken")
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0e\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"


@pytest.fixture(autouse=True)
def _vtu_settings(settings, tmp_path):
    for k, v in LIVE.items():
        setattr(settings, k, v)
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
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
    """Stands in for VTUClient._raw: scripted (status, body) per 'METHOD /path'."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, path, payload=None, params=None, idempotency_key=None, timeout=45):
        self.calls.append({"method": method, "path": path, "payload": payload, "params": params,
                           "key": idempotency_key})
        result = self.responses.get(f"{method} {path}")
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if result else (404, {"error": {"code": "not_found", "message": "Not found"}})


def _tx(status, reference="BILL202611120830A1", **extra):
    return {"id": "8f7243e0", "reference": reference, "type": {"value": "bill_payment"},
            "status": {"value": status, "is_final": status != "processing"},
            "amount": {"raw": 500, "formatted": "NGN 500.00", "currency": "NGN"},
            "metadata": extra.pop("metadata", {}), "failure_reason": extra.pop("failure_reason", None),
            **extra}


def _created(status="processing", **extra):
    return 201, {"message": "Request received.", "transaction": _tx(status, **extra)}


def _buy_airtime(client, user, amount="500", method="wallet"):
    client.force_authenticate(user)
    return client.post("/api/v1/billing/orders/", {
        "service": "airtime", "provider": "mtn", "customer_id": "0803 123 4567",
        "amount": amount, "payment_method": method}, format="json")


def _webhook(client, payload, token="hooktoken"):
    return client.post(f"/api/v1/billing/webhook/vtu/{token}/", data=json.dumps(payload),
                       content_type="application/json")


# ---------------------------------------------------------------- core flow
def test_wallet_airtime_sends_partner_api_request(client, user):
    _fund(user, 1000)
    fake = FakeVTU({"POST /bills/airtime": _created("completed")})
    with patch.object(VTUClient, "_raw", fake):
        resp = _buy_airtime(client, user)
    assert resp.status_code == 201, resp.json()
    assert resp.json()["status"] == "successful"
    call = fake.calls[-1]
    assert call["payload"] == {"phone": "08031234567", "network": "mtn", "amount": 500}
    order = BillOrder.objects.get()
    assert call["key"] == order.request_id                 # Idempotency-Key = our request id
    assert order.provider_order_id == "BILL202611120830A1"
    assert Wallet.objects.get(user=user).balance == Decimal("500.00")


def test_processing_then_webhook_completes_after_api_check(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created()})):
        order_id = _buy_airtime(client, user).json()["id"]
    assert BillOrder.objects.get(pk=order_id).status == BillStatus.PROCESSING
    event = {"id": "evt_1", "event": "transaction.completed", "livemode": True,
             "data": {"object": "transaction", "reference": "BILL202611120830A1",
                      "status": "completed"}}
    lookup = FakeVTU({"GET /transactions/BILL202611120830A1": (200, {"data": _tx("completed")})})
    with patch.object(VTUClient, "_raw", lookup):
        assert _webhook(client, event, token="wrong").status_code == 404
        assert _webhook(client, event).status_code == 200
    assert BillOrder.objects.get(pk=order_id).status == BillStatus.SUCCESSFUL


def test_forged_webhook_cannot_complete_or_refund(client, user):
    """A webhook only triggers a lookup: VTU.ng's API decides the status."""
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created()})):
        order_id = _buy_airtime(client, user).json()["id"]
    forged = {"id": "evt_x", "event": "transaction.refunded", "livemode": True,
              "data": {"object": "transaction", "reference": "BILL202611120830A1",
                       "status": "refunded"}}
    lookup = FakeVTU({"GET /transactions/BILL202611120830A1": (200, {"data": _tx("processing")})})
    with patch.object(VTUClient, "_raw", lookup):
        _webhook(client, forged)
    assert BillOrder.objects.get(pk=order_id).status == BillStatus.PROCESSING
    assert Wallet.objects.get(user=user).balance == Decimal("500.00")     # no refund


def test_refund_webhook_refunds_once(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created()})):
        order_id = _buy_airtime(client, user).json()["id"]
    event = {"id": "evt_2", "event": "transaction.refunded", "livemode": True,
             "data": {"object": "transaction_refund", "transaction_reference": "BILL202611120830A1",
                      "transaction_status": "refunded"}}
    lookup = FakeVTU({"GET /transactions/BILL202611120830A1": (200, {"data": _tx(
        "refunded", failure_reason="Provider timeout")})})
    with patch.object(VTUClient, "_raw", lookup):
        for _ in range(2):          # delivered twice
            assert _webhook(client, event).status_code == 200
    order = BillOrder.objects.get(pk=order_id)
    assert order.status == BillStatus.REFUNDED and order.provider_message == "Provider timeout"
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")
    assert WalletTransaction.objects.filter(source="refund").count() == 1


def test_sandbox_webhook_is_trusted_and_live_events_are_ignored(client, user, settings):
    settings.VTU_BASE_URL = "https://sandbox.vtu.ng/v1"
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created()})):
        order_id = _buy_airtime(client, user).json()["id"]
    live = {"id": "e", "event": "transaction.completed", "livemode": True,
            "data": {"object": "transaction", "reference": "BILL202611120830A1", "status": "completed"}}
    _webhook(client, live)
    assert BillOrder.objects.get(pk=order_id).status == BillStatus.PROCESSING
    _webhook(client, {**live, "livemode": False})
    assert BillOrder.objects.get(pk=order_id).status == BillStatus.SUCCESSFUL


def test_insufficient_wallet_balance(client, user):
    _fund(user, 100)
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        resp = _buy_airtime(client, user)
    assert resp.status_code == 400
    assert BillOrder.objects.count() == 0            # rolled back with the failed debit


def test_definite_refusal_refunds_wallet(client, user):
    _fund(user, 1000)
    fake = FakeVTU({"POST /bills/airtime": (422, {"error": {
        "code": "insufficient_funds", "message": "Insufficient wallet balance for this transaction."}})})
    with patch.object(VTUClient, "_raw", fake):
        resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "refunded"
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")
    assert WalletTransaction.objects.filter(source="refund").count() == 1


def test_unknown_outcome_is_recovered_by_idempotency_key(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": (502, {})})):
        resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "processing"      # NOT refunded: the order may exist
    assert Wallet.objects.get(user=user).balance == Decimal("500.00")

    order = BillOrder.objects.get()
    BillOrder.objects.update(updated_at="2000-01-01T00:00:00Z")
    fake = FakeVTU({f"GET /transactions/{order.request_id}": (200, {"data": _tx("completed")})})
    with patch.object(VTUClient, "_raw", fake):
        assert requery_stale_orders() == 1
    assert fake.calls[-1]["params"] == {"type": "bill_payment"}
    order.refresh_from_db()
    assert order.status == BillStatus.SUCCESSFUL and order.provider_order_id == "BILL202611120830A1"


def test_order_never_received_is_resent_with_same_key(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": VTUUnknown("timeout")})):
        _buy_airtime(client, user)
    order = BillOrder.objects.get()
    BillOrder.objects.update(updated_at="2000-01-01T00:00:00Z")
    fake = FakeVTU({"POST /bills/airtime": _created("completed")})   # GET -> 404
    with patch.object(VTUClient, "_raw", fake):
        requery_stale_orders()
    post = [c for c in fake.calls if c["method"] == "POST"][0]
    assert post["key"] == order.request_id            # same key: VTU.ng can't charge twice
    assert BillOrder.objects.get().status == BillStatus.SUCCESSFUL


def test_order_missing_for_30_minutes_is_refunded(client, user):
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": VTUUnknown("timeout")})):
        _buy_airtime(client, user)
    BillOrder.objects.update(updated_at="2000-01-01T00:00:00Z", sent_at="2000-01-01T00:00:00Z",
                             created_at=__import__("django.utils.timezone", fromlist=["now"]).now())
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        requery_stale_orders()
    assert BillOrder.objects.get().status == BillStatus.REFUNDED
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")


@override_settings(VTU_API_KEY="")
def test_unconfigured_biller_refunds(client, user):
    _fund(user, 1000)
    resp = _buy_airtime(client, user)
    assert resp.json()["status"] == "refunded"
    assert Wallet.objects.get(user=user).balance == Decimal("1000.00")


# ---------------------------------------------------------------- plans + prices
def test_data_price_comes_from_biller_not_client(client, user):
    _fund(user, 5000)
    fake = FakeVTU({
        "GET /bills/data-plans": (200, {"data": [
            {"variation_code": "mtn-1gb-799", "name": "MTN 1GB 30 Days", "amount": 799,
             "fixed_price": True}]}),
        "POST /bills/data": _created("completed"),
    })
    client.force_authenticate(user)
    with patch.object(VTUClient, "_raw", fake):
        plans = client.get("/api/v1/billing/variations/data/?provider=mtn").json()
        resp = client.post("/api/v1/billing/orders/", {
            "service": "data", "provider": "mtn", "customer_id": "08031234567",
            "variation_id": "mtn-1gb-799", "amount": "1", "payment_method": "wallet"}, format="json")
    assert plans == [{"variation_id": "mtn-1gb-799", "data_plan": "MTN 1GB 30 Days",
                      "price": "799", "availability": "Available"}]
    assert resp.status_code == 201, resp.json()
    assert resp.json()["amount"] == "799.00"
    assert fake.calls[-1]["payload"] == {"phone": "08031234567", "network": "mtn",
                                         "variation_code": "mtn-1gb-799"}


def test_electricity_verify_and_token(client, user):
    _fund(user, 10000)
    client.force_authenticate(user)
    verify = FakeVTU({"POST /bills/electricity/verify-meter": (200, {
        "message": "Meter verified successfully.", "verification": {
            "customer_name": "ENGR JOHN DOE", "meter_number": "12345671234", "meter_type": "prepaid",
            "address": "12 EXAMPLE STREET IKEJA", "customer_arrears": None,
            "minimum_purchase_amount": 500}})})
    with patch.object(VTUClient, "_raw", verify):
        info = client.post("/api/v1/billing/verify-customer/", {
            "service": "electricity", "provider": "ikedc", "customer_id": "12345671234",
            "meter_type": "prepaid"}, format="json").json()
    assert info == {"customer_name": "ENGR JOHN DOE", "customer_address": "12 EXAMPLE STREET IKEJA",
                    "min_purchase_amount": 500}
    buy = FakeVTU({"POST /bills/electricity": _created("completed", metadata={
        "electricity_token": "1234 5678 9012 3456 7890", "electricity_units": "45.0"})})
    with patch.object(VTUClient, "_raw", buy):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "electricity", "provider": "ikedc", "customer_id": "12345671234",
            "variation_id": "prepaid", "amount": "5000", "payment_method": "wallet"}, format="json")
    body = resp.json()
    assert body["status"] == "successful" and body["token"] == "1234 5678 9012 3456 7890"
    assert body["units"] == "45.0"
    assert buy.calls[-1]["payload"] == {"disco_slug": "ikedc", "meter_number": "12345671234",
                                        "meter_type": "prepaid", "amount": 5000}


def test_tv_renewal_uses_decoder_price(client, user):
    _fund(user, 30000)
    client.force_authenticate(user)
    fake = FakeVTU({
        "POST /bills/cable-tv/verify-smartcard": (200, {"verification": {
            "customer_name": "JOHN DOE", "current_bouquet": "DStv Compact",
            "current_bouquet_price": 19000, "renewal_amount": 18500, "supports_renewal": True}}),
        "POST /bills/cable-tv": _created("completed"),
    })
    with patch.object(VTUClient, "_raw", fake):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "tv", "provider": "dstv", "customer_id": "10001234",
            "subscription_type": "renew", "payment_method": "wallet"}, format="json")
    assert resp.status_code == 201, resp.json()
    assert resp.json()["amount"] == "18500.00" and resp.json()["customer_name"] == "JOHN DOE"
    assert fake.calls[-1]["payload"] == {"provider": "dstv", "smartcard_number": "10001234",
                                         "quantity": 1, "subscription_type": "renew",
                                         "amount": 18500}


def test_airtime_limits(client, user):
    _fund(user, 100000)
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        assert _buy_airtime(client, user, amount="60000").status_code == 400
        assert _buy_airtime(client, user, amount="20.5").status_code == 400


# ---------------------------------------------------------------- card payments
@override_settings(FLUTTERWAVE_SECRET_KEY="FLWSECK_TEST")
@patch("apps.payments.services.get_gateway")
def test_card_payment_dispatches_after_confirmation(mock_gw, client, user,
                                                    django_capture_on_commit_callbacks):
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://pay/x"}
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        resp = _buy_airtime(client, user, method="card")
    assert resp.json()["status"] == "awaiting_payment"
    assert resp.json()["authorization_url"] == "https://pay/x"
    payment = Payment.objects.get(purpose="bill_payment")
    assert payment.gateway == "flutterwave"
    mock_gw.assert_called_with("flutterwave")

    fake = FakeVTU({"POST /bills/airtime": _created("completed")})
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


@override_settings(FLUTTERWAVE_SECRET_KEY="FLWSECK_TEST", PAYSTACK_SECRET_KEY="sk_test")
@patch("apps.payments.services.get_gateway")
def test_bills_and_topups_always_use_flutterwave(mock_gw, client, user):
    """Even if the client asks for Paystack, bills and top-ups go to Flutterwave."""
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://flw/x"}
    client.force_authenticate(user)
    resp = client.post("/api/v1/billing/wallet/fund/", {"amount": "2000", "gateway": "paystack"},
                       format="json")
    assert resp.status_code == 201
    assert Payment.objects.get(reference=resp.json()["reference"]).gateway == "flutterwave"
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "airtime", "provider": "mtn", "customer_id": "08031234567",
            "amount": "500", "payment_method": "card", "gateway": "paystack"}, format="json")
    assert resp.status_code == 201
    assert Payment.objects.get(purpose="bill_payment").gateway == "flutterwave"
    assert client.get("/api/v1/billing/catalogue/").json()["gateway"] == "flutterwave"


@override_settings(FLUTTERWAVE_SECRET_KEY="")
def test_flutterwave_not_configured_blocks_card_but_not_wallet(client, user):
    client.force_authenticate(user)
    resp = client.post("/api/v1/billing/wallet/fund/", {"amount": "2000"}, format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "billing_gateway_unavailable"
    assert Payment.objects.count() == 0
    _fund(user, 1000)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created("completed")})):
        assert _buy_airtime(client, user).json()["status"] == "successful"   # wallet still works


# ---------------------------------------------------------------- ePINs + betting
def test_epin_packs_stored_privately_and_served_to_owner_only(client, user):
    _fund(user, 20000)
    data_uri = "data:image/webp;base64," + base64.b64encode(WEBP).decode()
    cards = [{"id": "pack-1", "network": "MTN", "denomination": 500, "pieces_count": 10,
              "image_url": data_uri,
              "download_url": "https://api.vtu.ng/v1/bills/epin/vouchers/pack-1/image"}]
    fake = FakeVTU({"POST /bills/epin": _created("completed", epin_cards=cards)})
    client.force_authenticate(user)
    with patch.object(VTUClient, "_raw", fake):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "epins", "provider": "mtn", "variation_id": "500", "quantity": 2,
            "payment_method": "wallet"}, format="json")
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["status"] == "successful" and body["amount"] == "10000.00"   # 500 × 10 × 2
    assert fake.calls[-1]["payload"] == {"network": "mtn", "denomination": 500, "quantity": 2}
    assert body["epin_packs"][0]["pieces_count"] == 10
    order = BillOrder.objects.get()
    assert "image_url" not in json.dumps(order.response)           # images kept in one place only
    img = client.get(f"/api/v1/billing/orders/{order.id}/epin/0/")
    assert img.status_code == 200 and b"".join(img.streaming_content) == WEBP
    assert img["Cache-Control"] == "private, no-store"
    other = User.objects.create_user("o@x.com", "Str0ng!Pass9", full_name="O", role="customer")
    client.force_authenticate(other)
    assert client.get(f"/api/v1/billing/orders/{order.id}/epin/0/").status_code == 404


def test_epins_validation(client, user):
    _fund(user, 50000)
    client.force_authenticate(user)
    with patch.object(VTUClient, "_raw", FakeVTU({})):
        bad_value = client.post("/api/v1/billing/orders/", {
            "service": "epins", "provider": "mtn", "variation_id": "300", "quantity": 1,
            "payment_method": "wallet"}, format="json")
        too_many = client.post("/api/v1/billing/orders/", {
            "service": "epins", "provider": "mtn", "variation_id": "100", "quantity": 6,
            "payment_method": "wallet"}, format="json")
    assert bad_value.status_code == 400 and too_many.status_code == 400


def test_betting_funding_and_refund(client, user):
    _fund(user, 5000)
    client.force_authenticate(user)
    verify = FakeVTU({"POST /bills/bet-funding/verify-account": (200, {
        "message": "Account verified successfully.", "validation": {
            "name": "John Smith", "username": "jsmith", "account_identifier": "12345",
            "provider": "bet9ja", "provider_label": "Bet9ja", "min_payable_amount": 100}})})
    with patch.object(VTUClient, "_raw", verify):
        info = client.post("/api/v1/billing/verify-customer/", {
            "service": "betting", "provider": "bet9ja", "customer_id": "12345"}, format="json")
    assert info.json()["customer_name"] == "John Smith"
    ok = FakeVTU({"POST /bills/bet-funding": _created("completed")})
    with patch.object(VTUClient, "_raw", ok):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "betting", "provider": "bet9ja", "customer_id": "12345", "amount": "1500",
            "payment_method": "wallet"}, format="json")
    assert resp.json()["status"] == "successful"
    assert ok.calls[-1]["payload"] == {"provider": "bet9ja", "account_id": "12345", "amount": 1500}
    refused = FakeVTU({"POST /bills/bet-funding": (422, {"error": {
        "code": "validation_failed", "message": "Amount is below the minimum."}})})
    with patch.object(VTUClient, "_raw", refused):
        resp = client.post("/api/v1/billing/orders/", {
            "service": "betting", "provider": "bet9ja", "customer_id": "12345", "amount": "1000",
            "payment_method": "wallet"}, format="json")
    assert resp.json()["status"] == "refunded"
    assert Wallet.objects.get(user=user).balance == Decimal("3500.00")


@override_settings(BILL_BETTING_ENABLED=False)
def test_betting_can_be_switched_off(client, user):
    _fund(user, 5000)
    client.force_authenticate(user)
    assert client.get("/api/v1/billing/catalogue/").json()["betting"] == []
    resp = client.post("/api/v1/billing/orders/", {
        "service": "betting", "provider": "bet9ja", "customer_id": "12345", "amount": "1000",
        "payment_method": "wallet"}, format="json")
    assert resp.status_code == 400
