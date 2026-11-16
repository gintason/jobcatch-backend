"""Phase 2A tests: profile editing, NYSC fields, artisan job videos."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import ArtisanJobVideo, User

pytestmark = pytest.mark.django_db


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


def test_artisan_updates_own_profile(client):
    artisan = _user("a@x.com", "artisan")
    client.force_authenticate(artisan)
    resp = client.patch("/api/v1/me/profile/", {
        "bio": "Expert plumber", "service_radius_km": 25,
        "latitude": 9.06, "longitude": 7.49,   # Abuja
    }, format="json")
    assert resp.status_code == 200
    artisan.artisan_profile.refresh_from_db()
    assert artisan.artisan_profile.service_radius_km == 25
    assert artisan.artisan_profile.base_location is not None


def test_artisan_cannot_set_work_verified_flag(client):
    """is_work_verified is admin-controlled; a PATCH must not flip it."""
    artisan = _user("a2@x.com", "artisan")
    client.force_authenticate(artisan)
    resp = client.patch("/api/v1/me/profile/", {"is_work_verified": True}, format="json")
    assert resp.status_code == 200
    artisan.artisan_profile.refresh_from_db()
    assert artisan.artisan_profile.is_work_verified is False


def test_jobseeker_sets_graduate_and_nysc(client):
    seeker = _user("s@x.com", "job_seeker")
    client.force_authenticate(seeker)
    resp = client.patch("/api/v1/me/profile/", {
        "is_graduate": True, "nysc_status": "completed", "headline": "Backend dev",
    }, format="json")
    assert resp.status_code == 200
    seeker.job_seeker_profile.refresh_from_db()
    assert seeker.job_seeker_profile.is_graduate is True
    assert seeker.job_seeker_profile.nysc_status == "completed"


def test_artisan_uploads_job_video(client):
    artisan = _user("a3@x.com", "artisan")
    client.force_authenticate(artisan)
    video = SimpleUploadedFile("job.mp4", b"fake-bytes", content_type="video/mp4")
    resp = client.post("/api/v1/artisan/job-videos/", {
        "video": video, "title": "Kitchen sink install",
    }, format="multipart")
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"          # awaits admin approval
    assert ArtisanJobVideo.objects.filter(artisan=artisan.artisan_profile).count() == 1


def test_job_video_rejects_non_mp4(client):
    artisan = _user("a4@x.com", "artisan")
    client.force_authenticate(artisan)
    bad = SimpleUploadedFile("job.txt", b"nope", content_type="text/plain")
    resp = client.post("/api/v1/artisan/job-videos/",
                       {"video": bad, "title": "x"}, format="multipart")
    assert resp.status_code == 400


def test_job_videos_are_scoped_to_owner(client):
    a1 = _user("owner@x.com", "artisan")
    a2 = _user("other@x.com", "artisan")
    ArtisanJobVideo.objects.create(
        artisan=a1.artisan_profile,
        video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"),
        title="mine",
    )
    client.force_authenticate(a2)
    resp = client.get("/api/v1/artisan/job-videos/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0    # a2 sees none of a1's videos


def test_non_artisan_blocked_from_job_videos(client):
    customer = _user("c@x.com", "customer")
    client.force_authenticate(customer)
    resp = client.get("/api/v1/artisan/job-videos/")
    assert resp.status_code == 403
