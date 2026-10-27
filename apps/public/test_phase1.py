"""Phase 1: 60 artisans per page, job detail + posted date + verified badge, photo deletion."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import ArtisanPortfolioItem, User
from apps.catalog.models import Category, Service
from apps.jobs.models import Job

pytestmark = pytest.mark.django_db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
       b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def client():
    return APIClient()


def _artisan(i, cat):
    u = User.objects.create_user(f"a{i}@x.com", "Str0ng!Pass9", full_name=f"A{i}", role="artisan")
    Service.objects.create(artisan=u.artisan_profile, category=cat, title="S",
                           description="d", base_price=1000)
    return u


def test_artisan_browse_returns_60_per_page(client):
    cat = Category.objects.create(name="Plumbing")
    for i in range(65):
        _artisan(i, cat)
    data = client.get("/api/v1/public/artisans/?category=plumbing").json()
    assert data["count"] == 65
    assert len(data["results"]) == 60
    assert data["next"]


def test_public_job_detail_has_posted_date_badge_and_formatting(client):
    emp = User.objects.create_user("e@x.com", "Str0ng!Pass9", full_name="Acme", role="employer")
    emp.employer_profile.is_cac_verified = True
    emp.employer_profile.save()
    job = Job.objects.create(employer=emp.employer_profile, title="Dev",
                             description="Line one\n\nLine two\n  - bullet")
    data = client.get(f"/api/v1/public/jobs/{job.id}/").json()
    assert data["description"] == "Line one\n\nLine two\n  - bullet"
    assert data["employer_verified"] is True
    assert data["posted_at"].startswith(str(job.published_at.date()))


def test_draft_job_detail_is_not_public(client):
    emp = User.objects.create_user("e2@x.com", "Str0ng!Pass9", full_name="Acme", role="employer")
    job = Job.objects.create(employer=emp.employer_profile, title="Draft", description="d",
                             published_at=None)
    assert client.get(f"/api/v1/public/jobs/{job.id}/").status_code == 404


def test_artisan_deletes_photo_and_file(client, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    u = User.objects.create_user("ph@x.com", "Str0ng!Pass9", full_name="P", role="artisan")
    client.force_authenticate(u)
    item_id = client.post("/api/v1/artisan/portfolio/",
                          {"image": SimpleUploadedFile("w.png", PNG, content_type="image/png"),
                           "caption": "x"}, format="multipart").json()["id"]
    item = ArtisanPortfolioItem.objects.get(pk=item_id)
    path = item.image.path
    import os

    assert os.path.exists(path)
    assert client.delete(f"/api/v1/artisan/portfolio/{item_id}/").status_code == 204
    assert not os.path.exists(path)
    assert not ArtisanPortfolioItem.objects.filter(pk=item_id).exists()


def test_cannot_delete_someone_elses_photo(client, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    owner = User.objects.create_user("o@x.com", "Str0ng!Pass9", full_name="O", role="artisan")
    item = ArtisanPortfolioItem.objects.create(
        artisan=owner.artisan_profile,
        image=SimpleUploadedFile("w.png", PNG, content_type="image/png"))
    other = User.objects.create_user("x@x.com", "Str0ng!Pass9", full_name="X", role="artisan")
    client.force_authenticate(other)
    assert client.delete(f"/api/v1/artisan/portfolio/{item.id}/").status_code == 404


def test_private_media_never_served(client, tmp_path, settings):
    from apps.common.media import serve_public_media
    from django.http import Http404
    from django.test import RequestFactory

    settings.MEDIA_ROOT = str(tmp_path)
    (tmp_path / "_private").mkdir()
    (tmp_path / "_private" / "id.pdf").write_bytes(b"secret")
    req = RequestFactory().get("/")
    for path in ("_private/id.pdf", "x/../_private/id.pdf", "./_private/id.pdf"):
        with pytest.raises(Http404):
            serve_public_media(req, path)
