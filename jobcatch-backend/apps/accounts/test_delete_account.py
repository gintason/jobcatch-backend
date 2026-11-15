"""Self-service account deletion (App Store / Google Play requirement)."""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db
PW = "Str0ng!Pass9"


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()


def _login(email="ada@example.com"):
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": email, "password": PW}, format="json")
    assert r.status_code == 200, r.json()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    return c, r.json()["refresh"]


def _user():
    u = User.objects.create_user("ada@example.com", PW, full_name="Ada Obi", phone="08031234567")
    u.is_email_verified = True
    u.save()
    return u


def test_wrong_password_keeps_the_account():
    u = _user()
    c, _ = _login()
    r = c.post("/api/v1/auth/delete-account/", {"password": "nope"}, format="json")
    assert r.status_code == 400
    u.refresh_from_db()
    assert u.is_active and u.email == "ada@example.com"


def test_delete_anonymises_deactivates_and_ends_sessions():
    u = _user()
    c, refresh = _login()
    r = c.post("/api/v1/auth/delete-account/", {"password": PW}, format="json")
    assert r.status_code == 200, r.json()
    u.refresh_from_db()
    assert not u.is_active and u.full_name == "Deleted user" and u.phone is None
    assert u.email.startswith("deleted-") and not u.has_usable_password()
    # The old access token and refresh token no longer work, and the email can't sign in.
    assert c.get("/api/v1/auth/me/").status_code == 401
    assert APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json").status_code != 200
    assert APIClient().post("/api/v1/auth/login/", {"email": "ada@example.com", "password": PW}, format="json").status_code != 200
    # The address is free to register again.
    assert not User.objects.filter(email="ada@example.com").exists()


def test_google_only_account_confirms_by_typing_delete():
    u = User(email="g@example.com", full_name="G User", is_email_verified=True)
    u.set_unusable_password()
    u.save()
    from apps.accounts.tokens import tokens_for_user
    _, access = tokens_for_user(u)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert c.post("/api/v1/auth/delete-account/", {}, format="json").status_code == 400
    assert c.post("/api/v1/auth/delete-account/", {"confirm": "delete"}, format="json").status_code == 200
    u.refresh_from_db()
    assert not u.is_active


def test_logout_all_devices_also_kills_refresh_tokens():
    _user()
    c, refresh = _login()
    assert c.post("/api/v1/auth/logout-all/").status_code == 200
    r = APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert r.status_code == 401
