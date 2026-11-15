"""Paid (₦5,000) artisan verification -> Verified Artisan badge."""
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category
from apps.payments.models import Payment
from apps.payments.services import confirm_payment
from apps.verification.models import ArtisanVerificationApplication

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def private_media(tmp_path, settings):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def artisan():
    return User.objects.create_user("art@x.com", "Str0ng!Pass9", full_name="Bola Ade",
                                    role="artisan", is_email_verified=True)


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("admin@x.com", "Str0ng!Pass9", full_name="A",
                                         role="admin", is_staff=True)


def _fill(client, artisan):
    cat = Category.objects.create(name="Plumbing")
    client.force_authenticate(artisan)
    resp = client.patch("/api/v1/artisan-verification/", {
        "categories": [str(cat.id)], "years_experience": 6, "id_type": "nin",
        "id_number": "12345678901", "trade_credentials": "City & Guilds plumbing"},
        format="json")
    assert resp.status_code == 200, resp.json()
    resp = client.post("/api/v1/artisan-verification/documents/", {
        "doc_type": "government_id",
        "file": SimpleUploadedFile("id.pdf", b"%PDF-1.4", content_type="application/pdf")},
        format="multipart")
    assert resp.status_code == 201, resp.json()


def test_incomplete_application_cannot_submit(client, artisan):
    client.force_authenticate(artisan)
    resp = client.post("/api/v1/artisan-verification/submit/", {}, format="json")
    assert resp.status_code == 400
    assert "missing" in resp.json()


@patch("apps.payments.services.get_gateway")
def test_fee_then_review_then_badge(mock_gw, client, artisan, admin_user):
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://pay/x"}
    _fill(client, artisan)
    data = client.post("/api/v1/artisan-verification/submit/", {}, format="json").json()
    assert data["status"] == "awaiting_payment"
    assert data["authorization_url"] == "https://pay/x"
    payment = Payment.objects.get(purpose="verification")
    assert payment.amount == 5000

    confirm_payment(payment.reference, source="test", paid_amount=payment.amount)
    app = ArtisanVerificationApplication.objects.get()
    assert app.status == "pending_review" and app.fee_paid

    client.force_authenticate(admin_user)
    assert client.get("/api/v1/admin/artisan-verifications/?status=pending_review").json()["count"] == 1
    assert client.post(f"/api/v1/admin/artisan-verifications/{app.id}/reject/", {},
                       format="json").status_code == 400          # reason required
    resp = client.post(f"/api/v1/admin/artisan-verifications/{app.id}/approve/", {}, format="json")
    assert resp.json()["status"] == "verified"
    artisan.artisan_profile.refresh_from_db()
    assert artisan.artisan_profile.is_work_verified is True


@patch("apps.payments.services.get_gateway")
def test_resubmission_after_rejection_is_free(mock_gw, client, artisan, admin_user):
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://pay/x"}
    _fill(client, artisan)
    client.post("/api/v1/artisan-verification/submit/", {}, format="json")
    payment = Payment.objects.get(purpose="verification")
    confirm_payment(payment.reference, source="test", paid_amount=payment.amount)
    app = ArtisanVerificationApplication.objects.get()
    client.force_authenticate(admin_user)
    client.post(f"/api/v1/admin/artisan-verifications/{app.id}/reject/",
                {"note": "ID photo unreadable"}, format="json")
    client.force_authenticate(artisan)
    data = client.post("/api/v1/artisan-verification/submit/", {}, format="json").json()
    assert data["status"] == "pending_review" and data["authorization_url"] is None
    assert Payment.objects.filter(purpose="verification").count() == 1
