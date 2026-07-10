"""Tests for the public (unauthenticated) home-page endpoints."""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, Service

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def test_featured_artisans_public_no_auth(client):
    a = User.objects.create_user("art@x.com", "Str0ng!Pass9", full_name="Ada Artisan",
                                 role="artisan", is_email_verified=True)
    cat = Category.objects.create(name="Plumbing")
    Service.objects.create(artisan=a.artisan_profile, category=cat,
                           title="Pipes", description="d", base_price=5000)
    resp = client.get("/api/v1/public/featured-artisans/")  # no auth header
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["full_name"] == "Ada Artisan"
    assert data[0]["trade"] == "Plumbing"
    assert data[0]["is_verified"] is False


def test_categories_public_with_counts(client):
    cat = Category.objects.create(name="Cleaning")
    a = User.objects.create_user("a2@x.com", "Str0ng!Pass9", full_name="A", role="artisan",
                                 is_email_verified=True)
    Service.objects.create(artisan=a.artisan_profile, category=cat, title="Clean",
                           description="d", base_price=1000)
    resp = client.get("/api/v1/public/categories/")
    assert resp.status_code == 200
    names = {c["name"]: c["service_count"] for c in resp.json()}
    assert names.get("Cleaning") == 1


def test_artisan_browse_search(client):
    a = User.objects.create_user("plumber@x.com", "Str0ng!Pass9", full_name="Bola Plumber",
                                 role="artisan", is_email_verified=True)
    b = User.objects.create_user("elec@x.com", "Str0ng!Pass9", full_name="Emeka Electric",
                                 role="artisan", is_email_verified=True)
    plumbing = Category.objects.create(name="Plumbing")
    electrical = Category.objects.create(name="Electrical")
    Service.objects.create(artisan=a.artisan_profile, category=plumbing, title="Pipes",
                           description="d", base_price=5000)
    Service.objects.create(artisan=b.artisan_profile, category=electrical, title="Wiring",
                           description="d", base_price=6000)

    # search by name
    r = client.get("/api/v1/public/artisans/?q=Bola")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    # filter by category slug
    r2 = client.get(f"/api/v1/public/artisans/?category={plumbing.slug}")
    assert r2.json()["count"] == 1
    assert r2.json()["results"][0]["full_name"] == "Bola Plumber"


def test_artisan_detail_public(client):
    a = User.objects.create_user("det@x.com", "Str0ng!Pass9", full_name="Ada Detail",
                                 role="artisan", is_email_verified=True)
    cat = Category.objects.create(name="Cleaning")
    Service.objects.create(artisan=a.artisan_profile, category=cat, title="Deep clean",
                           description="Full house", base_price=8000)
    r = client.get(f"/api/v1/public/artisans/{a.artisan_profile.id}/")
    assert r.status_code == 200
    data = r.json()
    assert data["full_name"] == "Ada Detail"
    assert len(data["services"]) == 1
    assert data["services"][0]["category_name"] == "Cleaning"
