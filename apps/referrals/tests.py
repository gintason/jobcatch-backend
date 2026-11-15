"""Referral codes, sign-up linking and once-only rewards after a real purchase."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillOrder, BillStatus, Wallet
from apps.billing.services import _post
from apps.billing.vtu import VTUClient
from apps.billing.tests import FakeVTU, _created
from apps.referrals.models import Referral, ReferralStatus

pytestmark = pytest.mark.django_db(transaction=True)
PW = "Str0ng!Pass9"


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()


def _user(email, verified=True, **kw):
    u = User.objects.create_user(email, PW, full_name=kw.pop("name", "Ada Obi"), **kw)
    u.is_email_verified = verified
    u.save()
    return u


def _code(user):
    c = APIClient()
    c.force_authenticate(user)
    return c.get("/api/v1/referrals/").json()["code"]


def _register(code, email="new@example.com"):
    return APIClient().post("/api/v1/auth/register/", {
        "full_name": "Bayo Lawal", "email": email, "password": PW, "referral_code": code}, format="json")


def _balance(u):
    w = Wallet.objects.filter(user=u).first()
    return w.balance if w else Decimal("0")


def _buy_airtime(user, amount):
    from apps.billing.models import TxnKind, TxnSource
    _post(user, kind=TxnKind.CREDIT, source=TxnSource.ADJUSTMENT, amount=amount, reference=f"seed:{user.pk}:{amount}",
          description="seed")
    c = APIClient()
    c.force_authenticate(user)
    with patch.object(VTUClient, "_raw", FakeVTU({"POST /bills/airtime": _created("completed")})):
        r = c.post("/api/v1/billing/orders/", {"service": "airtime", "provider": "mtn", "customer_id": "08031234567",
                                                "amount": str(amount), "payment_method": "wallet"}, format="json")
    assert r.status_code == 201, r.json()
    return r


def test_summary_gives_stable_code_and_link():
    ada = _user("ada@example.com")
    c = APIClient(); c.force_authenticate(ada)
    a, b = c.get("/api/v1/referrals/").json(), c.get("/api/v1/referrals/").json()
    assert a["code"] == b["code"] and len(a["code"]) == 7
    assert a["link"].endswith(f"/register?ref={a['code']}") and a["reward"] == "500"


def test_signup_with_code_pays_inviter_only_after_first_qualifying_purchase():
    ada = _user("ada@example.com")
    r = _register(_code(ada).lower())                     # codes are case-insensitive
    assert r.status_code == 201, r.json()
    bayo = User.objects.get(email="new@example.com")
    ref = Referral.objects.get(referee=bayo)
    assert ref.referrer == ada and ref.status == ReferralStatus.PENDING
    bayo.is_email_verified = True; bayo.save()
    _buy_airtime(bayo, 500)                                # below the ₦1,000 minimum
    ref.refresh_from_db()
    assert ref.status == ReferralStatus.PENDING and _balance(ada) == 0
    _buy_airtime(bayo, 1000)
    ref.refresh_from_db()
    assert ref.status == ReferralStatus.REWARDED and _balance(ada) == Decimal("500")
    _buy_airtime(bayo, 2000)                               # never paid twice
    assert _balance(ada) == Decimal("500")
    summary = APIClient(); summary.force_authenticate(ada)
    body = summary.get("/api/v1/referrals/").json()
    assert body["stats"] == {"invited": 1, "rewarded": 1, "pending": 0, "earned": "500.00"}
    assert body["referrals"][0]["name"] == "Bayo L."     # invitee's surname stays private


def test_invalid_or_own_code_is_refused():
    assert _register("NOPE123").status_code == 400
    ada = _user("ada@example.com")
    c = APIClient(); c.force_authenticate(ada)
    code = c.get("/api/v1/referrals/").json()["code"]
    assert c.post("/api/v1/referrals/apply/", {"code": code}, format="json").status_code == 400


@override_settings(REFERRAL_NEW_USER_BONUS="200")
def test_late_code_within_window_and_bonus_for_new_user():
    ada = _user("ada@example.com")
    bayo = _user("bayo@example.com", name="Bayo Lawal")
    c = APIClient(); c.force_authenticate(bayo)
    r = c.post("/api/v1/referrals/apply/", {"code": _code(ada)}, format="json")
    assert r.status_code == 200 and r.json()["referred_by"] == "Ada O." and not r.json()["can_apply"]
    assert c.post("/api/v1/referrals/apply/", {"code": _code(ada)}, format="json").status_code == 400  # only once
    _buy_airtime(bayo, 1500)
    assert _balance(ada) == Decimal("500")
    assert _balance(bayo) == Decimal("200")                # 1500 seeded − 1500 spent + 200 bonus


def test_late_code_refused_after_window_or_after_first_purchase():
    ada = _user("ada@example.com")
    old = _user("old@example.com")
    User.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=30))
    c = APIClient(); c.force_authenticate(User.objects.get(pk=old.pk))
    assert c.post("/api/v1/referrals/apply/", {"code": _code(ada)}, format="json").status_code == 400
    spender = _user("spend@example.com")
    _buy_airtime(spender, 1000)
    c.force_authenticate(spender)
    assert c.post("/api/v1/referrals/apply/", {"code": _code(ada)}, format="json").status_code == 400


@override_settings(REFERRAL_MONTHLY_CAP=1)
def test_monthly_cap_stops_reward_farming():
    ada = _user("ada@example.com")
    code = _code(ada)
    for i in range(2):
        u = _user(f"f{i}@example.com", name=f"Friend {i}")
        c = APIClient(); c.force_authenticate(u)
        c.post("/api/v1/referrals/apply/", {"code": code}, format="json")
        _buy_airtime(u, 1000)
    assert _balance(ada) == Decimal("500")
    assert Referral.objects.filter(status=ReferralStatus.REJECTED).count() == 1


def test_unverified_invitee_does_not_trigger_reward():
    ada = _user("ada@example.com")
    _register(_code(ada))
    bayo = User.objects.get(email="new@example.com")       # never verified their email
    _buy_airtime(bayo, 1000)
    assert _balance(ada) == 0
