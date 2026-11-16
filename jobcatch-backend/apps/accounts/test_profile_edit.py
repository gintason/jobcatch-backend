"""Edit profile (name, phone, photo) and change password."""
import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db
PW = "Str0ng!Pass9"


@pytest.fixture(autouse=True)
def _clear(tmp_path, settings):
    cache.clear()
    settings.MEDIA_ROOT = tmp_path


def _png():
    # 1x1 PNG
    data = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                         "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
    return SimpleUploadedFile("me.png", data, content_type="image/png")


def _login(email="ada@example.com"):
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": email, "password": PW}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    return c


def _user(email="ada@example.com", phone=None):
    u = User.objects.create_user(email, PW, full_name="Ada Obi", phone=phone)
    u.is_email_verified = True
    u.save()
    return u


def test_edit_name_phone_and_photo():
    u = _user()
    c = _login()
    r = c.patch("/api/v1/auth/me/", {"full_name": "  Ada   Okafor ", "phone": "0803 123 4567"}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["full_name"] == "Ada Okafor" and r.json()["phone"] == "08031234567"
    assert r.json()["email"] == "ada@example.com" and r.json()["has_password"] is True
    r = c.patch("/api/v1/auth/me/", {"photo": _png()}, format="multipart")
    assert r.status_code == 200 and r.json()["photo"].startswith("/media/avatars/")
    old = User.objects.get(pk=u.pk).photo.name
    r = c.patch("/api/v1/auth/me/", {"remove_photo": True}, format="json")
    assert r.json()["photo"] is None
    from django.core.files.storage import default_storage
    assert not default_storage.exists(old)                      # replaced photos are cleaned up


def test_cannot_take_someone_elses_phone_or_change_email_or_role():
    _user("bayo@example.com", phone="08031234567")
    u = _user()
    c = _login()
    assert c.patch("/api/v1/auth/me/", {"phone": "08031234567"}, format="json").status_code == 400
    c.patch("/api/v1/auth/me/", {"email": "evil@example.com", "role": "admin", "is_identity_verified": True}, format="json")
    u.refresh_from_db()
    assert u.email == "ada@example.com" and u.role != "admin" and not u.is_identity_verified


def test_non_image_photo_is_rejected():
    _user()
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    assert _login().patch("/api/v1/auth/me/", {"photo": bad}, format="multipart").status_code == 400


def test_change_password_keeps_this_device_and_signs_out_others():
    _user()
    other = _login()
    c = _login()
    assert c.post("/api/v1/auth/change-password/", {"current_password": "wrong", "new_password": "N3w!Passw0rd"},
                  format="json").status_code == 400
    r = c.post("/api/v1/auth/change-password/", {"current_password": PW, "new_password": "N3w!Passw0rd"}, format="json")
    assert r.status_code == 200 and r.json()["access"]
    assert other.get("/api/v1/auth/me/").status_code == 401                 # other device signed out
    fresh = APIClient(); fresh.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    assert fresh.get("/api/v1/auth/me/").status_code == 200                 # this one keeps going
    assert APIClient().post("/api/v1/auth/login/", {"email": "ada@example.com", "password": "N3w!Passw0rd"},
                            format="json").status_code == 200


def test_google_only_account_can_set_a_first_password():
    u = User(email="g@example.com", full_name="G User", is_email_verified=True)
    u.set_unusable_password(); u.save()
    from apps.accounts.tokens import tokens_for_user
    _, access = tokens_for_user(u)
    c = APIClient(); c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert c.get("/api/v1/auth/me/").json()["has_password"] is False
    assert c.post("/api/v1/auth/change-password/", {"new_password": "N3w!Passw0rd"}, format="json").status_code == 200
    u.refresh_from_db()
    assert u.check_password("N3w!Passw0rd")
