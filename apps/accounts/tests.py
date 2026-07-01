"""
Phase 1 auth + RBAC tests.

Run: DJANGO_SETTINGS_MODULE=config.settings.test pytest apps/accounts/tests.py
(requires a PostGIS test database).
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import OTP, OTPPurpose, User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def _latest_code(user, purpose):
    """Tests can't read the emailed code, so re-issue and grab plaintext directly."""
    otp, code = OTP.issue(user, purpose, 6, 10)
    return code


def test_register_creates_user_and_profile(client):
    resp = client.post(reverse("register"), {
        "email": "art@example.com", "full_name": "Ada Artisan",
        "role": "artisan", "password": "Str0ng!Pass9",
    }, format="json")
    assert resp.status_code == 201
    user = User.objects.get(email="art@example.com")
    assert user.is_email_verified is False
    assert user.role == UserRole.ARTISAN
    assert hasattr(user, "artisan_profile")  # signal created the profile


def test_admin_cannot_self_register(client):
    resp = client.post(reverse("register"), {
        "email": "hacker@example.com", "full_name": "X",
        "role": "admin", "password": "Str0ng!Pass9",
    }, format="json")
    assert resp.status_code == 400


def test_login_blocked_until_verified(client):
    user = User.objects.create_user("cust@example.com", "Str0ng!Pass9",
                                     full_name="Cee", role="customer")
    resp = client.post(reverse("login"),
                       {"email": "cust@example.com", "password": "Str0ng!Pass9"}, format="json")
    assert resp.status_code == 403
    assert resp.json().get("code") == "email_unverified"


def test_verify_then_authenticated_access(client):
    user = User.objects.create_user("cust2@example.com", "Str0ng!Pass9",
                                     full_name="Cee", role="customer")
    code = _latest_code(user, OTPPurpose.EMAIL_VERIFY)
    resp = client.post(reverse("verify-otp"),
                       {"email": user.email, "code": code, "purpose": "email_verify"},
                       format="json")
    assert resp.status_code == 200
    access = resp.json()["access"]

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    me = client.get(reverse("me"))
    assert me.status_code == 200
    assert me.json()["role"] == "customer"
    assert me.json()["profile"] is not None


def test_logout_all_revokes_existing_token(client):
    user = User.objects.create_user("cust3@example.com", "Str0ng!Pass9",
                                     full_name="Cee", role="customer",
                                     is_email_verified=True)
    login = client.post(reverse("login"),
                        {"email": user.email, "password": "Str0ng!Pass9"}, format="json")
    access = login.json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert client.post(reverse("logout-all")).status_code == 200
    # Same token must now be rejected by the token_version check.
    assert client.get(reverse("me")).status_code == 401


def test_rbac_role_permission():
    """The IsArtisan class only passes for artisan users."""
    from apps.common.permissions import IsArtisan

    class Req:
        pass

    artisan = User.objects.create_user("a@example.com", "x", full_name="A", role="artisan")
    customer = User.objects.create_user("c@example.com", "x", full_name="C", role="customer")

    r = Req(); r.user = artisan
    assert IsArtisan().has_permission(r, None) is True
    r.user = customer
    assert IsArtisan().has_permission(r, None) is False
