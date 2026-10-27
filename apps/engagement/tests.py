"""Upgrade prompts, job seeker Pro checkout, WhatsApp job alerts, nudges."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, CategoryKind
from apps.engagement.models import JobAlertDelivery, JobAlertSubscription
from apps.engagement.services import send_upgrade_nudges
from apps.jobs.models import Job
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.payments.services import confirm_payment
from apps.subscriptions.models import Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0],
                                    role=role, is_email_verified=True)


def _make_pro(user):
    return Subscription.objects.create(user=user, plan="seeker_pro", is_active=True,
                                       started_at=timezone.now(),
                                       expires_at=timezone.now() + timedelta(days=30))


def test_seeker_sees_one_popup_then_cooldown(client):
    seeker = _user("s@x.com", "job_seeker")
    client.force_authenticate(seeker)
    prompts = client.get("/api/v1/engagement/prompts/?audience=job_seeker").json()
    styles = [p["style"] for p in prompts]
    assert styles.count("modal") == 1 and "banner" in styles
    modal = next(p for p in prompts if p["style"] == "modal")
    assert modal["cta_url"] == "/checkout/seeker-pro"
    client.post(f"/api/v1/engagement/prompts/{modal['key']}/seen/")
    again = client.get("/api/v1/engagement/prompts/?audience=job_seeker").json()
    assert all(p["style"] != "modal" for p in again)        # capped by cooldown


def test_pro_members_get_no_upgrade_prompts(client):
    seeker = _user("p@x.com", "job_seeker")
    _make_pro(seeker)
    client.force_authenticate(seeker)
    assert client.get("/api/v1/engagement/prompts/?audience=job_seeker").json() == []


def test_dismissed_banner_hidden(client):
    seeker = _user("d@x.com", "job_seeker")
    client.force_authenticate(seeker)
    client.post("/api/v1/engagement/prompts/seeker-pro-banner/dismiss/")
    keys = [p["key"] for p in client.get("/api/v1/engagement/prompts/?audience=job_seeker").json()]
    assert "seeker-pro-banner" not in keys


@patch("apps.payments.services.get_gateway")
def test_seeker_pro_checkout_activates_for_30_days(mock_gw, client):
    mock_gw.return_value.initialize.return_value = {"authorization_url": "https://pay/x"}
    seeker = _user("c@x.com", "job_seeker")
    client.force_authenticate(seeker)
    resp = client.post("/api/v1/engagement/seeker-pro/checkout/", {}, format="json")
    assert resp.status_code == 201
    payment = Payment.objects.get(reference=resp.json()["reference"])
    assert payment.amount == 3000
    confirm_payment(payment.reference, source="test", paid_amount=payment.amount)
    status = client.get("/api/v1/engagement/seeker-pro/").json()
    assert status["is_pro"] is True


def test_job_alerts_only_to_matching_pro_seekers(django_capture_on_commit_callbacks):
    it = Category.objects.create(name="IT", kind=CategoryKind.JOB)
    sales = Category.objects.create(name="Sales", kind=CategoryKind.JOB)
    employer = _user("e@x.com", "employer")
    pro_it = _user("pro1@x.com", "job_seeker")
    pro_sales = _user("pro2@x.com", "job_seeker")
    free_it = _user("free@x.com", "job_seeker")
    for u, cat in ((pro_it, it), (pro_sales, sales), (free_it, it)):
        sub = JobAlertSubscription.objects.create(user=u, whatsapp_number="08031234567")
        sub.categories.add(cat)
    _make_pro(pro_it)
    _make_pro(pro_sales)

    from apps.listings.services import publish_job
    from django.test import override_settings

    employer.employer_profile.is_cac_verified = True
    employer.employer_profile.save()
    job = Job.objects.create(employer=employer.employer_profile, title="Django dev",
                             description="d", category=it, published_at=None)
    with override_settings(PAID_JOB_LISTINGS_ENABLED=False), \
            patch("apps.notifications.whatsapp.send_template") as wa, \
            django_capture_on_commit_callbacks(execute=True):
        publish_job(job)
    assert wa.call_count == 1
    assert JobAlertDelivery.objects.filter(job=job).count() == 1
    assert Notification.objects.filter(user=pro_it, kind="job_alert").exists()
    assert not Notification.objects.filter(user=free_it, kind="job_alert").exists()


def test_weekly_nudge_sent_once():
    _user("n@x.com", "job_seeker")
    assert send_upgrade_nudges() == 1
    assert send_upgrade_nudges() == 0
