"""Phase 3B tests: event-driven notifications + inbox endpoints."""
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bookings.models import Booking, BookingStatus
from apps.bookings.services import transition_booking
from apps.catalog.models import Category, Service
from apps.jobs.models import Application, ApplicationStatus, CV, Job
from apps.jobs.services import transition_application
from apps.notifications.models import Notification
from apps.notifications.services import notify
from apps.payments.models import Payment, PaymentPurpose, PaymentStatus
from apps.verification.models import Verification, VerificationStatus, VerificationType

pytestmark = pytest.mark.django_db


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0],
                                    role=role, is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


# ---------------------------------------------------------------- event triggers
def test_booking_transition_notifies_customer(client):
    customer = _user("c@x.com", "customer")
    artisan = _user("a@x.com", "artisan")
    cat = Category.objects.create(name="Plumbing")
    service = Service.objects.create(artisan=artisan.artisan_profile, category=cat,
                                     title="Pipe", description="d", base_price=5000)
    booking = Booking.objects.create(
        customer=customer.customer_profile, artisan=artisan.artisan_profile, service=service,
        scheduled_for=timezone.now() + timedelta(days=1), location=Point(7.49, 9.06),
        address="Abuja", agreed_price=5000,
    )
    transition_booking(booking, BookingStatus.ACCEPTED, actor=artisan)
    # artisan acted -> customer is notified
    assert Notification.objects.filter(user=customer, kind="booking_alert").count() == 1


def test_new_application_notifies_employer(client):
    employer = _user("emp@x.com", "employer")
    seeker = _user("seek@x.com", "job_seeker")
    job = Job.objects.create(employer=employer.employer_profile, title="Dev", description="d")
    cv = CV.objects.create(seeker=seeker.job_seeker_profile,
                           file="cvs/x.pdf", title="CV")
    Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=cv)
    assert Notification.objects.filter(user=employer, kind="application_alert").count() == 1


def test_application_status_change_notifies_seeker(client):
    employer = _user("emp2@x.com", "employer")
    seeker = _user("seek2@x.com", "job_seeker")
    job = Job.objects.create(employer=employer.employer_profile, title="Dev", description="d")
    cv = CV.objects.create(seeker=seeker.job_seeker_profile, file="cvs/x.pdf", title="CV")
    app = Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=cv)
    Notification.objects.all().delete()  # clear the "new application" one
    transition_application(app, ApplicationStatus.SHORTLISTED)
    assert Notification.objects.filter(user=seeker, kind="application_alert").count() == 1


def test_payment_success_notifies_payer(client):
    payer = _user("pay@x.com", "customer")
    payment = Payment.objects.create(payer=payer, purpose=PaymentPurpose.BOOKING,
                                     gateway="paystack", reference="R", amount=1000,
                                     status=PaymentStatus.INITIATED)
    payment.status = PaymentStatus.SUCCESS
    payment.save(update_fields=["status", "updated_at"])
    assert Notification.objects.filter(user=payer, kind="payment_alert").count() == 1


def test_verification_result_notifies_user(client):
    user = _user("v@x.com", "customer")
    v = Verification.objects.create(user=user, type=VerificationType.IDENTITY,
                                    document="verifications/x.pdf")
    v.status = VerificationStatus.APPROVED
    v.save(update_fields=["status", "updated_at"])
    assert Notification.objects.filter(user=user, kind="verification_alert").count() == 1


# ---------------------------------------------------------------- inbox endpoints
def test_list_and_unread_filter(client):
    user = _user("u@x.com", "customer")
    notify(user, kind="test", title="One")
    n2 = notify(user, kind="test", title="Two")
    n2.is_read = True
    n2.save(update_fields=["is_read"])
    client.force_authenticate(user)

    assert client.get("/api/v1/notifications/").json()["count"] == 2
    assert client.get("/api/v1/notifications/?unread=true").json()["count"] == 1


def test_unread_count_and_mark_read(client):
    user = _user("u2@x.com", "customer")
    n = notify(user, kind="test", title="Hi")
    client.force_authenticate(user)

    assert client.get("/api/v1/notifications/unread-count/").json()["unread"] == 1
    assert client.post(f"/api/v1/notifications/{n.id}/read/").status_code == 200
    assert client.get("/api/v1/notifications/unread-count/").json()["unread"] == 0


def test_read_all(client):
    user = _user("u3@x.com", "customer")
    notify(user, kind="test", title="A")
    notify(user, kind="test", title="B")
    client.force_authenticate(user)
    resp = client.post("/api/v1/notifications/read-all/")
    assert resp.json()["marked_read"] == 2
    assert client.get("/api/v1/notifications/unread-count/").json()["unread"] == 0


def test_notifications_scoped_to_user(client):
    u1 = _user("u4@x.com", "customer")
    u2 = _user("u5@x.com", "customer")
    notify(u1, kind="test", title="mine")
    notify(u2, kind="test", title="theirs")
    client.force_authenticate(u1)
    assert client.get("/api/v1/notifications/").json()["count"] == 1
