"""Employer KYC: contact OTPs, documents, automated checks, review, badge, access control."""
import re
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.common.models import AuditLog
from apps.kyc.models import EmployerKYC, KYCDocument
from apps.kyc.providers import DojahProvider, names_match

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def private_media(tmp_path, settings):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def employer():
    return User.objects.create_user("boss@acme.ng", "Str0ng!Pass9", full_name="Ada Obi",
                                    role="employer", is_email_verified=True)


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("admin@x.com", "Str0ng!Pass9", full_name="Admin",
                                         role="admin", is_staff=True)


DETAILS = {
    "registered_name": "Acme Nigeria Limited", "company_type": "RC", "cac_number": "RC 1234567",
    "tin": "12345678-0001", "director_name": "Ada Obi", "director_nin": "12345678901",
    "business_address": "12 Allen Avenue, Ikeja", "state": "Lagos",
    "contact_email": "hr@acme.ng", "contact_phone": "0803 123 4567",
}


def _pdf(name="doc.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def _code_from_mail():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


def _complete(client, employer):
    client.force_authenticate(employer)
    assert client.patch("/api/v1/kyc/employer/", DETAILS, format="json").status_code == 200
    client.post("/api/v1/kyc/employer/otp/send/", {"channel": "email"}, format="json")
    client.post("/api/v1/kyc/employer/otp/verify/",
                {"channel": "email", "code": _code_from_mail()}, format="json")
    sms = []
    with patch("apps.kyc.services.send_sms", lambda phone, msg: sms.append((phone, msg))):
        client.post("/api/v1/kyc/employer/otp/send/", {"channel": "sms"}, format="json")
    assert sms[0][0] == "08031234567"
    code = re.search(r"\b(\d{6})\b", sms[0][1]).group(1)
    # Word for word the template Termii approved for the "OE Alert" sender ID.
    assert sms[0][1] == (f"Your Jobcatch verification code is {code}. This code expires in 10 minutes. "
                         "Do not share with anyone.")
    resp = client.post("/api/v1/kyc/employer/otp/verify/", {"channel": "sms", "code": code},
                       format="json")
    assert resp.json()["is_phone_verified"] is True
    for doc_type in ("cac_certificate", "utility_bill", "government_id"):
        resp = client.post("/api/v1/kyc/employer/documents/",
                           {"doc_type": doc_type, "file": _pdf()}, format="multipart")
        assert resp.status_code == 201, resp.json()


def test_details_are_normalised_and_nin_masked(client, employer):
    client.force_authenticate(employer)
    data = client.patch("/api/v1/kyc/employer/", DETAILS, format="json").json()
    assert data["cac_number"] == "1234567"
    assert data["contact_phone"] == "08031234567"
    assert "director_nin" not in data and data["director_nin_masked"].endswith("8901")
    assert data["status"] == "in_progress"


def test_invalid_nin_rejected(client, employer):
    client.force_authenticate(employer)
    resp = client.patch("/api/v1/kyc/employer/", {"director_nin": "123"}, format="json")
    assert resp.status_code == 400


def test_email_otp_verifies_only_that_address(client, employer):
    client.force_authenticate(employer)
    client.patch("/api/v1/kyc/employer/", {"contact_email": "hr@acme.ng"}, format="json")
    client.post("/api/v1/kyc/employer/otp/send/", {"channel": "email"}, format="json")
    assert mail.outbox[-1].to == ["hr@acme.ng"]
    code = _code_from_mail()
    assert client.post("/api/v1/kyc/employer/otp/verify/",
                       {"channel": "email", "code": "000000"}, format="json").status_code == 400
    data = client.post("/api/v1/kyc/employer/otp/verify/",
                       {"channel": "email", "code": code}, format="json").json()
    assert data["is_email_verified"] is True
    # Changing the address afterwards un-verifies it.
    data = client.patch("/api/v1/kyc/employer/", {"contact_email": "other@acme.ng"},
                        format="json").json()
    assert data["is_email_verified"] is False


def test_cannot_submit_incomplete(client, employer):
    client.force_authenticate(employer)
    client.patch("/api/v1/kyc/employer/", DETAILS, format="json")
    resp = client.post("/api/v1/kyc/employer/submit/")
    assert resp.status_code == 400
    assert "requirements" in resp.json()


def test_full_flow_manual_review_and_badge(client, employer, admin_user):
    _complete(client, employer)
    resp = client.post("/api/v1/kyc/employer/submit/")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "pending"
    assert resp.json()["checks"]["cac"]["status"] == "skipped"   # manual provider

    kyc = EmployerKYC.objects.get(employer=employer.employer_profile)
    # Locked while under review.
    assert client.patch("/api/v1/kyc/employer/", {"tin": "99999999-0001"},
                        format="json").status_code == 400
    # Still can't post jobs.
    assert client.post("/api/v1/jobs/", {"title": "x", "description": "y"},
                       format="json").status_code == 403

    client.force_authenticate(admin_user)
    queue = client.get("/api/v1/admin/kyc/?status=pending").json()
    assert queue["count"] == 1
    detail = client.get(f"/api/v1/admin/kyc/{kyc.id}/").json()
    assert detail["director_nin"] == "12345678901"            # unmasked for reviewers
    assert any(h["action"] == "kyc.submitted" for h in detail["history"])

    resp = client.post(f"/api/v1/admin/kyc/{kyc.id}/approve/", {"note": "Docs match"},
                       format="json")
    assert resp.json()["status"] == "verified"
    employer.employer_profile.refresh_from_db()
    assert employer.employer_profile.is_cac_verified is True

    client.force_authenticate(employer)
    assert client.post("/api/v1/jobs/", {"title": "x", "description": "y"},
                       format="json").status_code == 201


def test_reject_requires_reason_and_reopens_editing(client, employer, admin_user):
    _complete(client, employer)
    client.post("/api/v1/kyc/employer/submit/")
    kyc = EmployerKYC.objects.get(employer=employer.employer_profile)
    client.force_authenticate(admin_user)
    assert client.post(f"/api/v1/admin/kyc/{kyc.id}/reject/", {}, format="json").status_code == 400
    resp = client.post(f"/api/v1/admin/kyc/{kyc.id}/reject/",
                       {"reason": "Utility bill is older than 3 months"}, format="json")
    assert resp.json()["status"] == "rejected"
    client.force_authenticate(employer)
    data = client.get("/api/v1/kyc/employer/").json()
    assert data["is_editable"] is True
    assert "older than 3 months" in data["rejection_reason"]


@override_settings(KYC_PROVIDER="dojah", KYC_AUTO_APPROVE=True,
                   DOJAH_APP_ID="app", DOJAH_SECRET_KEY="sk")
def test_dojah_checks_pass_and_auto_approve(client, employer):
    _complete(client, employer)

    def fake_get(self, path, params):
        if "cac" in path:
            return 200, {"entity": {"company_name": "ACME NIGERIA LTD", "rc_number": "1234567",
                                    "company_status": "ACTIVE"}}
        if "tin" in path:
            return 200, {"entity": {"taxpayer_name": "ACME NIGERIA LIMITED",
                                    "cac_reg_number": "RC1234567"}}
        return 200, {"entity": {"first_name": "ADA", "last_name": "OBI",
                                "photo": "BASE64...", "date_of_birth": "1990-01-01"}}

    with patch.object(DojahProvider, "_get", fake_get):
        data = client.post("/api/v1/kyc/employer/submit/").json()
    assert data["status"] == "verified"
    kyc = EmployerKYC.objects.get(employer=employer.employer_profile)
    assert "photo" not in str(kyc.checks)                       # sensitive fields not stored
    assert kyc.is_manual_override is False


@override_settings(KYC_PROVIDER="dojah", KYC_AUTO_APPROVE=True,
                   DOJAH_APP_ID="app", DOJAH_SECRET_KEY="sk")
def test_dojah_name_mismatch_goes_to_review(client, employer):
    _complete(client, employer)

    def fake_get(self, path, params):
        if "cac" in path:
            return 200, {"entity": {"company_name": "TOTALLY DIFFERENT VENTURES"}}
        return 404, {"error": "Not found"}

    with patch.object(DojahProvider, "_get", fake_get):
        data = client.post("/api/v1/kyc/employer/submit/").json()
    assert data["status"] == "pending"
    assert data["checks"]["cac"]["status"] == "failed"
    assert data["checks"]["nin"]["status"] == "failed"


def test_names_match():
    assert names_match("Acme Nigeria Limited", "ACME NIG. LTD")
    assert names_match("Ada Obi", "OBI ADA CHIOMA")
    assert not names_match("Acme Limited", "Zenith Ventures")


def test_documents_are_private(client, employer, admin_user):
    other = User.objects.create_user("x@y.com", "Str0ng!Pass9", full_name="X", role="employer",
                                     is_email_verified=True)
    client.force_authenticate(employer)
    doc_id = client.post("/api/v1/kyc/employer/documents/",
                         {"doc_type": "utility_bill", "file": _pdf("bill.pdf")},
                         format="multipart").json()["id"]
    doc = KYCDocument.objects.get(pk=doc_id)
    assert "bill" not in doc.file.name                          # randomised storage name
    assert client.get(f"/api/v1/kyc/documents/{doc_id}/download/").status_code == 200
    client.force_authenticate(other)
    assert client.get(f"/api/v1/kyc/documents/{doc_id}/download/").status_code == 404
    client.force_authenticate(admin_user)
    assert client.get(f"/api/v1/kyc/documents/{doc_id}/download/").status_code == 200
    assert AuditLog.objects.filter(action="kyc.document_viewed").exists()


def test_non_admin_blocked_from_review_api(client, employer):
    client.force_authenticate(employer)
    assert client.get("/api/v1/admin/kyc/").status_code == 403
