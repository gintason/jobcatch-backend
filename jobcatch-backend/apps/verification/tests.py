"""Phase 3A tests: verification submission, admin approval, and badge propagation."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import ArtisanJobVideo, User
from apps.verification.models import Verification, VerificationStatus, VerificationType

pytestmark = pytest.mark.django_db


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True)


def _admin():
    return User.objects.create_superuser("admin@x.com", "Str0ng!Pass9", full_name="A")


@pytest.fixture
def client():
    return APIClient()


def _doc(name="id.pdf", ctype="application/pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4", content_type=ctype)


def test_user_submits_verification(client):
    user = _user("u@x.com", "customer")
    client.force_authenticate(user)
    resp = client.post("/api/v1/verifications/",
                       {"type": "identity", "document": _doc()}, format="multipart")
    assert resp.status_code == 201
    v = Verification.objects.get(user=user)
    assert v.status == VerificationStatus.PENDING


def test_user_sees_only_own_verifications(client):
    u1 = _user("u1@x.com", "customer")
    u2 = _user("u2@x.com", "customer")
    Verification.objects.create(user=u1, type=VerificationType.IDENTITY, document=_doc())
    Verification.objects.create(user=u2, type=VerificationType.IDENTITY, document=_doc())
    client.force_authenticate(u1)
    assert client.get("/api/v1/verifications/").json()["count"] == 1


def test_admin_approve_identity_sets_badge(client):
    user = _user("cust@x.com", "customer")
    v = Verification.objects.create(user=user, type=VerificationType.IDENTITY, document=_doc())
    client.force_authenticate(_admin())
    resp = client.post(f"/api/v1/verifications/{v.id}/approve/")
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.is_identity_verified is True


def test_admin_approve_cac_sets_employer_badge(client):
    employer = _user("emp@x.com", "employer")
    v = Verification.objects.create(user=employer, type=VerificationType.CAC, document=_doc())
    client.force_authenticate(_admin())
    client.post(f"/api/v1/verifications/{v.id}/approve/")
    employer.employer_profile.refresh_from_db()
    assert employer.employer_profile.is_cac_verified is True


def test_non_admin_cannot_approve(client):
    user = _user("u3@x.com", "customer")
    v = Verification.objects.create(user=user, type=VerificationType.IDENTITY, document=_doc())
    client.force_authenticate(user)
    resp = client.post(f"/api/v1/verifications/{v.id}/approve/")
    assert resp.status_code == 403
    user.refresh_from_db()
    assert user.is_identity_verified is False


def test_reject_records_status_and_note(client):
    user = _user("u4@x.com", "customer")
    v = Verification.objects.create(user=user, type=VerificationType.IDENTITY, document=_doc())
    client.force_authenticate(_admin())
    resp = client.post(f"/api/v1/verifications/{v.id}/reject/", {"note": "Blurry"}, format="json")
    assert resp.status_code == 200
    v.refresh_from_db()
    assert v.status == VerificationStatus.REJECTED
    assert v.review_note == "Blurry"


def test_admin_approves_job_video_sets_work_verified(client):
    artisan = _user("art@x.com", "artisan")
    video = ArtisanJobVideo.objects.create(
        artisan=artisan.artisan_profile,
        video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"),
        title="Tiling job",
    )
    client.force_authenticate(_admin())
    resp = client.post(f"/api/v1/admin/job-videos/{video.id}/approve/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    artisan.artisan_profile.refresh_from_db()
    assert artisan.artisan_profile.is_work_verified is True


def test_admin_job_video_queue_filters_pending(client):
    artisan = _user("art2@x.com", "artisan")
    ArtisanJobVideo.objects.create(
        artisan=artisan.artisan_profile,
        video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"), title="A")
    client.force_authenticate(_admin())
    resp = client.get("/api/v1/admin/job-videos/?status=pending")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_non_admin_blocked_from_job_video_queue(client):
    artisan = _user("art3@x.com", "artisan")
    client.force_authenticate(artisan)
    assert client.get("/api/v1/admin/job-videos/").status_code == 403
