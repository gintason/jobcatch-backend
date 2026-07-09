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
