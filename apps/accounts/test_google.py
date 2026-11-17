"""Sign in with Google (Google's tokeninfo call mocked)."""
import io
import json
import time
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db
WEB = "306763553702-jalc00es33k28etpj8shsufela5486t1.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()


def _google(**claims):
    info = {"aud": WEB, "iss": "https://accounts.google.com", "exp": str(int(time.time()) + 600),
            "email": "Ada@Gmail.com", "email_verified": "true", "name": "Ada Obi", "sub": "123", **claims}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return patch("apps.accounts.google.urllib.request.urlopen", return_value=Resp(json.dumps(info).encode()))


def _post(credential="tok"):
    return APIClient().post("/api/v1/auth/google/", {"credential": credential}, format="json")


def test_new_google_user_is_created_verified_and_signed_in():
    with _google():
        r = _post()
    assert r.status_code == 201, r.json()
    assert r.json()["access"] and r.json()["user"]["email"] == "ada@gmail.com"
    u = User.objects.get(email="ada@gmail.com")
    assert u.is_email_verified and u.full_name == "Ada Obi" and not u.has_usable_password()
    assert u.role == "customer"


def test_existing_account_signs_in_and_gets_verified():
    u = User.objects.create_user("ada@gmail.com", "Str0ng!Pass9", full_name="Ada", role="employer")
    assert not u.is_email_verified
    with _google():
        r = _post()
    assert r.status_code == 200 and r.json()["user"]["id"] == str(u.id)
    u.refresh_from_db()
    assert u.is_email_verified and u.check_password("Str0ng!Pass9")   # password kept


@pytest.mark.parametrize("claims", [{"aud": "someone-elses-app.apps.googleusercontent.com"},
                                    {"email_verified": "false"}, {"iss": "evil.example"},
                                    {"exp": "1"}])
def test_rejects_tokens_not_for_us_or_unverified(claims):
    with _google(**claims):
        r = _post()
    assert r.status_code == 400 and r.json()["code"] == "google_failed"
    assert not User.objects.exists()


def test_disabled_account_cannot_sign_in():
    User.objects.create_user("ada@gmail.com", "Str0ng!Pass9", full_name="Ada", role="customer",
                             is_active=False)
    with _google():
        assert _post().status_code == 403


def test_settings_ignore_spaces_and_always_include_jobcatch_clients(monkeypatch):
    """A GOOGLE_CLIENT_IDS value like 'a, b' (space after the comma) must not lock out the web client."""
    import importlib

    monkeypatch.setenv("GOOGLE_CLIENT_IDS", " other-app.apps.googleusercontent.com ,  ")
    from config.settings import base
    try:
        importlib.reload(base)
        assert "other-app.apps.googleusercontent.com" in base.GOOGLE_CLIENT_IDS
        assert WEB in base.GOOGLE_CLIENT_IDS
        assert all(c == c.strip() and c for c in base.GOOGLE_CLIENT_IDS)
    finally:
        monkeypatch.delenv("GOOGLE_CLIENT_IDS")
        importlib.reload(base)


def test_token_issued_to_the_ios_client_is_accepted():
    ios = "306763553702-5kvmuchb5s8ee3gjgpqb3mhnqeminfac.apps.googleusercontent.com"
    from django.conf import settings
    assert ios in settings.GOOGLE_CLIENT_IDS
    with _google(aud=ios):
        r = _post()
    assert r.status_code == 201, r.json()
