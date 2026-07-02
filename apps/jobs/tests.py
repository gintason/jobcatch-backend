"""Phase 2C tests: job posting, applications, duplicate guard, recruitment pipeline."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.jobs.models import Application, ApplicationStatus, CV, Job

pytestmark = pytest.mark.django_db


def _user(email, role):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def employer():
    return _user("emp@x.com", "employer")


@pytest.fixture
def seeker():
    return _user("seek@x.com", "job_seeker")


@pytest.fixture
def job(employer):
    return Job.objects.create(employer=employer.employer_profile,
                              title="Backend Dev", description="Django role")


def _cv(seeker, title="My CV"):
    return CV.objects.create(
        seeker=seeker.job_seeker_profile,
        file=SimpleUploadedFile("cv.pdf", b"%PDF-1.4", content_type="application/pdf"),
        title=title,
    )


# ---------------------------------------------------------------- jobs
def test_employer_creates_job(client, employer):
    client.force_authenticate(employer)
    resp = client.post("/api/v1/jobs/", {
        "title": "Plumber wanted", "description": "Full time", "salary_min": "80000",
    }, format="json")
    assert resp.status_code == 201
    assert Job.objects.filter(employer=employer.employer_profile).count() == 1


def test_non_employer_cannot_create_job(client, seeker):
    client.force_authenticate(seeker)
    resp = client.post("/api/v1/jobs/", {"title": "x", "description": "y"}, format="json")
    assert resp.status_code == 403


def test_anyone_browses_open_jobs(client, job, seeker):
    client.force_authenticate(seeker)
    resp = client.get("/api/v1/jobs/?q=backend")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


# ---------------------------------------------------------------- applications
def test_seeker_applies_with_cv(client, job, seeker):
    cv = _cv(seeker)
    client.force_authenticate(seeker)
    resp = client.post("/api/v1/applications/",
                       {"job": str(job.id), "cv": str(cv.id), "cover_letter": "Hi"},
                       format="json")
    assert resp.status_code == 201
    assert resp.json()["status"] == "submitted"


def test_duplicate_application_rejected(client, job, seeker):
    cv = _cv(seeker)
    client.force_authenticate(seeker)
    payload = {"job": str(job.id), "cv": str(cv.id)}
    assert client.post("/api/v1/applications/", payload, format="json").status_code == 201
    assert client.post("/api/v1/applications/", payload, format="json").status_code == 400


def test_cannot_apply_to_closed_job(client, job, seeker):
    job.is_open = False
    job.save()
    cv = _cv(seeker)
    client.force_authenticate(seeker)
    resp = client.post("/api/v1/applications/",
                       {"job": str(job.id), "cv": str(cv.id)}, format="json")
    assert resp.status_code == 400


def test_cannot_apply_with_someone_elses_cv(client, job, seeker):
    other = _user("other@x.com", "job_seeker")
    other_cv = _cv(other)
    client.force_authenticate(seeker)
    resp = client.post("/api/v1/applications/",
                       {"job": str(job.id), "cv": str(other_cv.id)}, format="json")
    assert resp.status_code == 400


def test_seeker_tracks_own_applications(client, job, seeker):
    Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    client.force_authenticate(seeker)
    resp = client.get("/api/v1/applications/")
    assert resp.json()["count"] == 1


# ---------------------------------------------------------------- pipeline
def test_employer_views_applicants_to_own_job(client, job, seeker, employer):
    Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    client.force_authenticate(employer)
    resp = client.get(f"/api/v1/jobs/{job.id}/applications/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_employer_shortlists_then_hires(client, job, seeker, employer):
    app = Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    client.force_authenticate(employer)
    assert client.post(f"/api/v1/applications/{app.id}/shortlist/").json()["status"] == "shortlisted"
    assert client.post(f"/api/v1/applications/{app.id}/hire/").json()["status"] == "hired"


def test_illegal_application_transition(client, job, seeker, employer):
    app = Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    client.force_authenticate(employer)
    # submitted -> hired is illegal (must shortlist first)
    resp = client.post(f"/api/v1/applications/{app.id}/hire/")
    assert resp.status_code == 400


def test_employer_cannot_touch_other_employers_application(client, job, seeker):
    app = Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    intruder = _user("emp2@x.com", "employer")
    client.force_authenticate(intruder)
    resp = client.post(f"/api/v1/applications/{app.id}/shortlist/")
    assert resp.status_code == 404


def test_seeker_cannot_change_application_status(client, job, seeker):
    app = Application.objects.create(job=job, seeker=seeker.job_seeker_profile, cv=_cv(seeker))
    client.force_authenticate(seeker)
    resp = client.post(f"/api/v1/applications/{app.id}/shortlist/")
    assert resp.status_code == 403
