"""Complaint box: public submission, validation, tracking, staff handling, attachment privacy."""
import pytest
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.complaints.models import Complaint
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def private_media(tmp_path, settings):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.COMPLAINTS_NOTIFY_EMAIL = "support@jobcatch.test"
    cache.clear()  # throttle counters


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("admin@x.com", "Str0ng!Pass9", full_name="Admin",
                                         role="admin", is_staff=True)


BILLING = {
    "category": "billing", "full_name": "Chika Eze", "email": "chika@example.com",
    "phone": "+234 803 123 4567", "subject": "Charged twice for airtime",
    "description": "I bought N1,000 MTN airtime and my card was debited twice.",
    "transaction_reference": "JCB-20261027-ABCD1234",
}


def _submit(client, **overrides):
    return client.post("/api/v1/complaints/", {**BILLING, **overrides}, format="json")


def test_anonymous_submission_records_contact_details_and_date(client, admin_user):
    res = _submit(client)
    assert res.status_code == 201, res.data
    c = Complaint.objects.get()
    assert res.data["reference"] == c.reference and c.reference.startswith("JCC-")
    assert c.phone == "08031234567" and c.email == "chika@example.com"
    assert c.created_at is not None and res.data["created_at"]
    assert c.status == "open" and c.user is None
    # acknowledgement to the complainant + alert to the support inbox + in-app alert to admins
    recipients = [m.to[0] for m in mail.outbox]
    assert "chika@example.com" in recipients and "support@jobcatch.test" in recipients
    assert Notification.objects.filter(user=admin_user, kind="admin_review").exists()


def test_signed_in_user_is_linked_and_sees_own_complaints(client):
    user = User.objects.create_user("seeker@x.com", "Str0ng!Pass9", full_name="Seeker",
                                    role="job_seeker")
    client.force_authenticate(user)
    assert _submit(client, email="seeker@x.com").status_code == 201
    assert Complaint.objects.get().user == user
    res = client.get("/api/v1/complaints/mine/")
    assert res.status_code == 200 and len(res.data) == 1
    assert "internal_note" not in res.data[0]


@pytest.mark.parametrize("overrides,field", [
    ({"phone": "12345"}, "phone"),
    ({"description": "too short"}, "description"),
    ({"transaction_reference": ""}, "transaction_reference"),
    ({"category": "artisan", "transaction_reference": ""}, "artisan_name"),
    ({"email": "not-an-email"}, "email"),
])
def test_validation(client, overrides, field):
    res = _submit(client, **overrides)
    assert res.status_code == 400 and field in res.data
    assert not Complaint.objects.exists()


def test_artisan_complaint_by_name(client):
    res = _submit(client, category="artisan", transaction_reference="",
                  artisan_name="Musa the plumber", subject="Did not finish the job")
    assert res.status_code == 201
    assert Complaint.objects.get().artisan_name == "Musa the plumber"


def test_honeypot_rejects_bots(client):
    assert _submit(client, website="http://spam").status_code == 400
    assert not Complaint.objects.exists()


def test_track_needs_reference_and_matching_email(client):
    ref = _submit(client).data["reference"]
    assert client.post("/api/v1/complaints/track/", {"reference": ref, "email": "x@y.com"}).status_code == 404
    res = client.post("/api/v1/complaints/track/", {"reference": ref.lower(), "email": "CHIKA@example.com"})
    assert res.status_code == 200 and res.data["status"] == "open"


def test_admin_responds_and_resolves(client, admin_user):
    ref = _submit(client).data["reference"]
    c = Complaint.objects.get(reference=ref)
    mail.outbox.clear()
    client.force_authenticate(admin_user)
    assert client.get("/api/v1/admin/complaints/?status=open").data["results"][0]["reference"] == ref
    res = client.patch(f"/api/v1/admin/complaints/{c.id}/", {
        "status": "resolved", "admin_response": "We refunded the duplicate debit.",
        "internal_note": "Flutterwave refund FLW-1"}, format="json")
    assert res.status_code == 200 and res.data["status"] == "resolved"
    c.refresh_from_db()
    assert c.resolved_at and c.responded_at and c.handled_by == admin_user
    assert mail.outbox[-1].to == ["chika@example.com"]
    assert "refunded the duplicate" in mail.outbox[-1].body
    assert "Flutterwave refund" not in mail.outbox[-1].body  # internal note stays internal
    assert any(h["action"] == "complaint.updated" for h in res.data["history"])
    # complainant sees the response but not the note
    client.force_authenticate(None)
    tracked = client.post("/api/v1/complaints/track/", {"reference": ref, "email": "chika@example.com"}).data
    assert tracked["admin_response"] == "We refunded the duplicate debit."
    assert "internal_note" not in tracked


def test_non_admin_cannot_see_complaints(client):
    _submit(client)
    user = User.objects.create_user("emp@x.com", "Str0ng!Pass9", full_name="E", role="employer")
    client.force_authenticate(user)
    assert client.get("/api/v1/admin/complaints/").status_code == 403


def test_attachment_is_private(client, admin_user):
    pdf = SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    res = client.post("/api/v1/complaints/", {**BILLING, "attachment": pdf}, format="multipart")
    assert res.status_code == 201, res.data
    c = Complaint.objects.get()
    assert c.attachment.name.startswith("complaints/")
    url = f"/api/v1/admin/complaints/{c.id}/attachment/"
    assert client.get(url).status_code in (401, 403)
    client.force_authenticate(admin_user)
    resp = client.get(url)
    assert resp.status_code == 200 and b"".join(resp.streaming_content).startswith(b"%PDF")


def test_admin_sees_the_bill_order_behind_a_receipt_number(client, admin_user):
    from decimal import Decimal

    from apps.billing.models import BillOrder

    buyer = User.objects.create_user("buyer@x.com", "Str0ng!Pass9", full_name="Buyer", role="job_seeker")
    order = BillOrder.objects.create(user=buyer, service="airtime", provider="mtn", customer_id="08031234567",
                                     amount=Decimal("1000"), total=Decimal("1000"), payment_method="wallet",
                                     request_id="REQ-1", status="successful")
    _submit(client, transaction_reference=order.receipt_number.lower())
    client.force_authenticate(admin_user)
    linked = client.get("/api/v1/admin/complaints/").data["results"][0]["linked_record"]
    assert linked["type"] == "bill_order" and linked["id"] == str(order.id)
    assert linked["user_email"] == "buyer@x.com"
