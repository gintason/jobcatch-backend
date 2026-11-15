"""PIN, wallet-to-wallet and wallet-to-bank transfers (Paystack faked)."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import TxnKind, TxnSource, Wallet, WalletTransaction
from apps.billing.services import _post
from apps.payments.gateways import GatewayError
from apps.transfers.models import BankTransfer, BankTransferStatus, TransferPin

pytestmark = pytest.mark.django_db
PW = "Str0ng!Pass9"
SECRET = "sk_test_transfers"


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()


class FakePaystack:
    def __init__(self, initiate=None, verify=None):
        self.initiate, self.verify = initiate or {"status": "pending"}, verify or {"status": "success"}
        self.calls = []

    def is_configured(self):
        return True

    def list_banks(self):
        return [{"name": "Access Bank", "code": "044"}, {"name": "GTBank", "code": "058"}]

    def resolve_account(self, *, account_number, bank_code):
        self.calls.append("resolve")
        if account_number == "0000000000":
            raise GatewayError("Could not resolve account name", http_status=422)
        return "ADA OBI"

    def create_transfer_recipient(self, **kw):
        self.calls.append("recipient")
        return "RCP_123"

    def initiate_transfer(self, **kw):
        self.calls.append(("initiate", kw["reference"]))
        if isinstance(self.initiate, Exception):
            raise self.initiate
        return {"status": self.initiate["status"], "transfer_code": "TRF_1", "message": "", "raw": self.initiate}

    def verify_transfer(self, reference):
        self.calls.append("verify")
        if isinstance(self.verify, Exception):
            raise self.verify
        return {"status": self.verify["status"], "transfer_code": "TRF_1", "reason": "", "raw": self.verify}


def _user(email="ada@example.com", phone=None, amount=0):
    u = User.objects.create_user(email, PW, full_name=email.split("@")[0].title() + " Obi", phone=phone)
    if amount:
        _post(u, kind=TxnKind.CREDIT, source=TxnSource.ADJUSTMENT, amount=amount, reference=f"seed:{email}",
              description="seed")
    return u


def _client(u):
    c = APIClient()
    c.force_authenticate(u)
    return c


def _with_pin(u, pin="2468"):
    r = _client(u).post("/api/v1/transfers/pin/", {"pin": pin, "password": PW}, format="json")
    assert r.status_code == 200, r.json()
    return u


def _bal(u):
    return Wallet.objects.get(user=u).balance


# ---------------------------------------------------------------- PIN
def test_pin_needs_password_rejects_weak_and_locks_after_five_wrong_tries():
    ada, bayo = _user(amount=5000), _user("bayo@example.com")
    c = _client(ada)
    assert c.post("/api/v1/transfers/pin/", {"pin": "2468"}, format="json").status_code == 400      # no password
    assert c.post("/api/v1/transfers/pin/", {"pin": "1234", "password": PW}, format="json").status_code == 400
    _with_pin(ada)
    assert TransferPin.objects.get(user=ada).pin_hash != "2468"                                       # hashed
    for i in range(5):
        r = c.post("/api/v1/transfers/wallet/", {"to": bayo.email, "amount": "500", "pin": "9999"}, format="json")
        assert r.status_code == 400
    assert "locked" in str(r.json())
    r = c.post("/api/v1/transfers/wallet/", {"to": bayo.email, "amount": "500", "pin": "2468"}, format="json")
    assert r.status_code == 400 and "Try again in" in str(r.json())                                  # even the right PIN
    assert _bal(ada) == Decimal("5000")


def test_change_pin_with_current_pin():
    ada = _with_pin(_user())
    c = _client(ada)
    assert c.post("/api/v1/transfers/pin/", {"pin": "1357"}, format="json").status_code == 400
    assert c.post("/api/v1/transfers/pin/", {"pin": "1357", "current_pin": "2468"}, format="json").status_code == 200


# ---------------------------------------------------------------- wallet → wallet
def test_wallet_transfer_by_phone_moves_money_and_notifies(django_capture_on_commit_callbacks):
    ada = _with_pin(_user(amount=10000))
    bayo = _user("bayo@example.com", phone="08031234567")
    c = _client(ada)
    look = c.get("/api/v1/transfers/lookup/", {"to": "+2348031234567"})
    assert look.status_code == 200 and look.json()["name"] == "Bayo Obi" and "•" in look.json()["email"]
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post("/api/v1/transfers/wallet/", {"to": "2348031234567", "amount": "2500", "pin": "2468",
                                                "note": "Lunch"}, format="json")
    assert r.status_code == 201, r.json()
    assert _bal(ada) == Decimal("7500") and _bal(bayo) == Decimal("2500")
    srcs = set(WalletTransaction.objects.values_list("source", flat=True))
    assert {"transfer_out", "transfer_in"} <= srcs
    from apps.notifications.models import Notification
    assert Notification.objects.filter(user=bayo, kind="transfer_in").exists()
    hist = _client(bayo).get("/api/v1/transfers/").json()
    assert hist[0]["type"] == "wallet_in" and hist[0]["counterparty"] == "Ada Obi"


def test_wallet_transfer_refuses_self_unknown_and_overdraft():
    ada = _with_pin(_user(amount=1000))
    _user("bayo@example.com")
    c = _client(ada)
    assert c.post("/api/v1/transfers/wallet/", {"to": ada.email, "amount": "500", "pin": "2468"}, format="json").status_code == 400
    assert c.post("/api/v1/transfers/wallet/", {"to": "nobody@x.com", "amount": "500", "pin": "2468"}, format="json").status_code == 400
    r = c.post("/api/v1/transfers/wallet/", {"to": "bayo@example.com", "amount": "5000", "pin": "2468"}, format="json")
    assert r.status_code == 400 and _bal(ada) == Decimal("1000")


def test_recent_card_topup_is_held_but_older_funds_move():
    ada = _with_pin(_user(amount=1000))
    _user("bayo@example.com")
    _post(ada, kind=TxnKind.CREDIT, source=TxnSource.TOPUP, amount=5000, reference="topup:1", description="card")
    c = _client(ada)
    ov = c.get("/api/v1/transfers/overview/").json()
    assert ov["balance"] == "6000.00" and ov["transferable"] == "1000.00" and ov["has_pin"]
    r = c.post("/api/v1/transfers/wallet/", {"to": "bayo@example.com", "amount": "3000", "pin": "2468"}, format="json")
    assert r.status_code == 400 and "funds_on_hold" in json.dumps(r.json())
    WalletTransaction.objects.filter(reference="topup:1").update(created_at=timezone.now() - timedelta(hours=25))
    assert c.post("/api/v1/transfers/wallet/", {"to": "bayo@example.com", "amount": "3000", "pin": "2468"},
                  format="json").status_code == 201


@override_settings(TRANSFER_DAILY_LIMIT="3000")
def test_daily_limit():
    ada = _with_pin(_user(amount=10000))
    _user("bayo@example.com")
    c = _client(ada)
    assert c.post("/api/v1/transfers/wallet/", {"to": "bayo@example.com", "amount": "2000", "pin": "2468"}, format="json").status_code == 201
    r = c.post("/api/v1/transfers/wallet/", {"to": "bayo@example.com", "amount": "1500", "pin": "2468"}, format="json")
    assert r.status_code == 400 and "daily" in str(r.json())


# ---------------------------------------------------------------- wallet → bank
def _send_bank(u, fake, amount="5000", account="0123456789"):
    with patch("apps.transfers.services.get_gateway", return_value=fake):
        return _client(u).post("/api/v1/transfers/bank/", {"bank_code": "044", "account_number": account,
                                                            "amount": amount, "pin": "2468"}, format="json")


def _webhook(event, reference):
    body = json.dumps({"event": event, "data": {"reference": reference, "status": event.split(".")[1]}}).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    with override_settings(PAYSTACK_SECRET_KEY=SECRET, PAYSTACK_WEBHOOK_SECRET=""):
        return APIClient().post("/api/v1/payments/webhook/paystack/", body, content_type="application/json",
                                HTTP_X_PAYSTACK_SIGNATURE=sig)


@override_settings(BANK_TRANSFER_FEE="50")
def test_bank_transfer_success_by_webhook():
    ada = _with_pin(_user(amount=10000))
    r = _send_bank(ada, FakePaystack())
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["status"] == "processing" and body["counterparty"] == "ADA OBI" and body["fee"] == "50.00"
    assert _bal(ada) == Decimal("4950")
    assert _webhook("transfer.success", body["reference"]).status_code == 200
    bt = BankTransfer.objects.get()
    assert bt.status == BankTransferStatus.SUCCESSFUL and bt.completed_at


def test_bank_transfer_failure_refunds_exactly_once():
    ada = _with_pin(_user(amount=10000))
    ref = _send_bank(ada, FakePaystack()).json()["reference"]
    _webhook("transfer.failed", ref)
    _webhook("transfer.failed", ref)                     # duplicate delivery
    assert BankTransfer.objects.get().status == BankTransferStatus.FAILED
    assert _bal(ada) == Decimal("10000")
    assert WalletTransaction.objects.filter(reference=f"{ref}:refund").count() == 1


def test_reversal_after_success_refunds():
    ada = _with_pin(_user(amount=10000))
    ref = _send_bank(ada, FakePaystack(initiate={"status": "success"})).json()["reference"]
    assert BankTransfer.objects.get().status == BankTransferStatus.SUCCESSFUL
    _webhook("transfer.reversed", ref)
    assert BankTransfer.objects.get().status == BankTransferStatus.REVERSED and _bal(ada) == Decimal("10000")


def test_paystack_rejection_refunds_at_once_and_otp_mode_refunds():
    ada = _with_pin(_user(amount=10000))
    r = _send_bank(ada, FakePaystack(initiate=GatewayError("Your balance is not enough", http_status=400)))
    assert r.json()["status"] == "failed" and _bal(ada) == Decimal("10000")
    r = _send_bank(ada, FakePaystack(initiate={"status": "otp"}))
    assert r.json()["status"] == "failed" and _bal(ada) == Decimal("10000")


def test_unknown_outcome_waits_then_requery_settles_it():
    from apps.transfers.services import requery_stale_bank_transfers
    ada = _with_pin(_user(amount=10000))
    r = _send_bank(ada, FakePaystack(initiate=GatewayError("timed out")))
    assert r.json()["status"] == "processing" and _bal(ada) == Decimal("4950")     # not refunded on a timeout
    BankTransfer.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    with patch("apps.transfers.services.get_gateway", return_value=FakePaystack(verify={"status": "success"})):
        assert requery_stale_bank_transfers() == 1
    assert BankTransfer.objects.get().status == BankTransferStatus.SUCCESSFUL


def test_bad_account_is_refused_before_any_debit():
    ada = _with_pin(_user(amount=10000))
    r = _send_bank(ada, FakePaystack(), account="0000000000")
    assert r.status_code == 400 and _bal(ada) == Decimal("10000") and not BankTransfer.objects.exists()
