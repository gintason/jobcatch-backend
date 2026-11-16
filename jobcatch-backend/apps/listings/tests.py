"""Paid job listings: package purchase, instant activation, credits, expiry."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.listings.models import JobPackage, PackagePurchase, PurchaseStatus
from apps.listings.services import expire_listings, publish_job
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import confirm_payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def employer():
    user = User.objects.create_user("emp@x.com", "Str0ng!Pass9", full_name="Acme",
                                    role="employer", is_email_verified=True)
    user.employer_profile.is_cac_verified = True
    user.employer_profile.save()
    return user


@pytest.fixture
def featured():
    return JobPackage.objects.get(slug="featured-post")   # seeded by migration


def _draft(employer, title="Backend Dev"):
    return Job.objects.create(employer=employer.employer_profile, title=title,
                              description="Role", published_at=None)


def _mock_gateway(mock):
    mock.return_value.initialize.return_value = {
        "authorization_url": "https://pay.test/x", "reference": "r"}


def test_packages_are_public(client):
    resp = client.get("/api/v1/listings/packages/")
    assert resp.status_code == 200
    slugs = [p["slug"] for p in resp.json()]
    assert "single-post" in slugs and "first-post-free" not in slugs   # inactive hidden


@patch("apps.payments.services.get_gateway")
def test_paying_for_a_job_publishes_it_instantly(mock_gw, client, employer, featured):
    _mock_gateway(mock_gw)
    job = _draft(employer)
    client.force_authenticate(employer)
    resp = client.post("/api/v1/listings/purchase/",
                       {"package": str(featured.id), "job": str(job.id)}, format="json")
    assert resp.status_code == 201, resp.json()
    assert resp.json()["authorization_url"] == "https://pay.test/x"
    job.refresh_from_db()
    assert job.listing_status == "draft"                      # not before payment

    payment = Payment.objects.get(reference=resp.json()["reference"])
    confirm_payment(payment.reference, source="test", paid_amount=payment.amount)

    job.refresh_from_db()
    assert job.listing_status == "active"
    assert job.is_featured is True
    assert job.expires_at > timezone.now() + timedelta(days=29)
    purchase = PackagePurchase.objects.get(payment=payment)
    assert purchase.credits_remaining == 0 and purchase.status == PurchaseStatus.EXHAUSTED


@patch("apps.payments.services.get_gateway")
def test_underpayment_delivers_nothing(mock_gw, client, employer, featured):
    _mock_gateway(mock_gw)
    job = _draft(employer)
    client.force_authenticate(employer)
    ref = client.post("/api/v1/listings/purchase/",
                      {"package": str(featured.id), "job": str(job.id)},
                      format="json").json()["reference"]
    confirm_payment(ref, source="test", paid_amount=Decimal("100"))
    assert Payment.objects.get(reference=ref).status == PaymentStatus.FAILED
    job.refresh_from_db()
    assert job.listing_status == "draft"


@patch("apps.payments.services.get_gateway")
def test_confirmation_is_idempotent(mock_gw, client, employer):
    _mock_gateway(mock_gw)
    bulk = JobPackage.objects.get(slug="bulk-5")
    client.force_authenticate(employer)
    ref = client.post("/api/v1/listings/purchase/", {"package": str(bulk.id)},
                      format="json").json()["reference"]
    confirm_payment(ref, source="webhook")
    confirm_payment(ref, source="verify")     # browser redirect races the webhook
    assert PackagePurchase.objects.get(payment__reference=ref).credits_total == 5
    assert PackagePurchase.objects.filter(employer=employer.employer_profile).count() == 1


def test_publish_requires_credit(client, employer):
    job = _draft(employer)
    client.force_authenticate(employer)
    resp = client.post(f"/api/v1/jobs/{job.id}/publish/")
    assert resp.status_code == 402
    assert resp.json()["code"] == "payment_required"


def test_publish_spends_one_credit(client, employer):
    bulk = JobPackage.objects.get(slug="bulk-5")
    purchase = PackagePurchase.objects.create(
        employer=employer.employer_profile, package=bulk, status=PurchaseStatus.ACTIVE,
        price=bulk.price, credits_total=5, listing_days=30)
    job = _draft(employer)
    client.force_authenticate(employer)
    resp = client.post(f"/api/v1/jobs/{job.id}/publish/")
    assert resp.status_code == 200
    assert resp.json()["listing_status"] == "active"
    purchase.refresh_from_db()
    assert purchase.credits_used == 1
    # Already live: publishing again doesn't burn another credit.
    client.post(f"/api/v1/jobs/{job.id}/publish/")
    purchase.refresh_from_db()
    assert purchase.credits_used == 1


def test_free_package_activates_without_checkout(client, employer):
    free = JobPackage.objects.get(slug="first-post-free")
    free.is_active = True
    free.save()
    job = _draft(employer)
    client.force_authenticate(employer)
    resp = client.post("/api/v1/listings/purchase/",
                       {"package": str(free.id), "job": str(job.id)}, format="json")
    assert resp.status_code == 201
    assert resp.json()["authorization_url"] is None
    assert resp.json()["job_published"] is True
    # limit_per_employer = 1
    again = client.post("/api/v1/listings/purchase/", {"package": str(free.id)}, format="json")
    assert again.status_code == 400


def test_expired_jobs_disappear_and_are_closed(client, employer):
    job = Job.objects.create(employer=employer.employer_profile, title="Old", description="d",
                             expires_at=timezone.now() - timedelta(minutes=1))
    assert Job.objects.live().count() == 0                    # hidden before the cron runs
    assert client.get("/api/v1/public/jobs/").json()["count"] == 0
    result = expire_listings()
    assert result["jobs_expired"] == 1
    job.refresh_from_db()
    assert job.is_open is False and job.listing_status == "expired"


@override_settings(PAID_JOB_LISTINGS_ENABLED=False)
def test_paid_listings_off_publishes_immediately(client, employer):
    client.force_authenticate(employer)
    resp = client.post("/api/v1/jobs/", {"title": "Free", "description": "d"}, format="json")
    assert resp.status_code == 201
    assert resp.json()["listing_status"] == "active"
    assert resp.json()["expires_at"] is None


def test_unverified_employer_cannot_publish(employer):
    employer.employer_profile.is_cac_verified = False
    employer.employer_profile.save()
    job = _draft(employer)
    with pytest.raises(Exception) as exc:
        publish_job(job)
    assert "kyc_required" in str(exc.value.detail)
