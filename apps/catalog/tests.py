"""Phase 2A tests: categories (admin-managed) and services (artisan-owned)."""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, Service

pytestmark = pytest.mark.django_db


def _user(email, role, **extra):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name="T", role=role,
                                    is_email_verified=True, **extra)


@pytest.fixture
def client():
    return APIClient()


def test_admin_creates_category(client):
    admin = User.objects.create_superuser("admin@x.com", "Str0ng!Pass9", full_name="A")
    client.force_authenticate(admin)
    resp = client.post("/api/v1/categories/", {"name": "Plumbing"}, format="json")
    assert resp.status_code == 201
    assert resp.json()["slug"] == "plumbing"


def test_non_admin_cannot_create_category(client):
    artisan = _user("a@x.com", "artisan")
    client.force_authenticate(artisan)
    resp = client.post("/api/v1/categories/", {"name": "Hacking"}, format="json")
    assert resp.status_code == 403


def test_authenticated_user_can_list_categories(client):
    Category.objects.create(name="Cleaning")
    customer = _user("c@x.com", "customer")
    client.force_authenticate(customer)
    resp = client.get("/api/v1/categories/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_artisan_creates_service_bound_to_self(client):
    cat = Category.objects.create(name="Electrical")
    artisan = _user("a2@x.com", "artisan")
    client.force_authenticate(artisan)
    resp = client.post("/api/v1/services/", {
        "category": str(cat.id), "title": "Wiring", "description": "Full rewire",
        "base_price": "15000.00",
    }, format="json")
    assert resp.status_code == 201
    svc = Service.objects.get(id=resp.json()["id"])
    assert svc.artisan == artisan.artisan_profile   # server-assigned, not client-supplied


def test_customer_can_browse_but_not_create_service(client):
    cat = Category.objects.create(name="Painting")
    artisan = _user("a3@x.com", "artisan")
    Service.objects.create(artisan=artisan.artisan_profile, category=cat,
                           title="Interior", description="d", base_price=5000)
    customer = _user("c2@x.com", "customer")
    client.force_authenticate(customer)

    assert client.get("/api/v1/services/").json()["count"] == 1        # can browse
    resp = client.post("/api/v1/services/", {
        "category": str(cat.id), "title": "x", "description": "y", "base_price": "1",
    }, format="json")
    assert resp.status_code == 403                                     # cannot create
