"""Phase 3C tests: geo-matching (distance filter, service radius, weighted ranking)."""
import pytest
from django.contrib.gis.geos import Point
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, Service

pytestmark = pytest.mark.django_db

# Customer location (Abuja). ~0.09 deg latitude ~= 10 km.
CX_LAT, CX_LNG = 9.06, 7.49


def _artisan(email, lat, lng, *, radius_km=10, available=True, featured=False,
             rating=0, work_verified=False):
    user = User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0],
                                    role="artisan", is_email_verified=True)
    p = user.artisan_profile
    p.base_location = Point(lng, lat)
    p.service_radius_km = radius_km
    p.is_available = available
    p.is_featured = featured
    p.avg_rating = rating
    p.is_work_verified = work_verified
    p.save()
    return user


def _customer():
    return User.objects.create_user("cust@x.com", "Str0ng!Pass9", full_name="C",
                                    role="customer", is_email_verified=True)


@pytest.fixture
def client():
    c = APIClient()
    c.force_authenticate(_customer())
    return c


def _match(client, **params):
    params.setdefault("latitude", CX_LAT)
    params.setdefault("longitude", CX_LNG)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/v1/matching/artisans/?{query}")


def test_requires_coordinates():
    c = APIClient()
    c.force_authenticate(_customer())
    assert c.get("/api/v1/matching/artisans/").status_code == 400


def test_returns_nearby_excludes_far(client):
    _artisan("near@x.com", 9.07, 7.49)          # ~1 km away
    _artisan("far@x.com", 9.60, 7.49)           # ~60 km away
    resp = _match(client, radius_km=20)
    assert resp.status_code == 200
    emails = [r["full_name"] for r in resp.json()]
    assert "near" in emails
    assert "far" not in emails


def test_respects_artisan_service_radius(client):
    # ~15 km away but only willing to travel 10 km -> excluded.
    _artisan("tooheavy@x.com", 9.195, 7.49, radius_km=10)
    resp = _match(client, radius_km=30)
    assert all(r["full_name"] != "tooheavy" for r in resp.json())


def test_unavailable_excluded(client):
    _artisan("off@x.com", 9.07, 7.49, available=False)
    resp = _match(client, radius_km=20)
    assert resp.json() == []


def test_featured_ranks_higher(client):
    _artisan("plain@x.com", 9.07, 7.49, featured=False, rating=3)
    _artisan("boosted@x.com", 9.07, 7.49, featured=True, rating=3)
    results = _match(client, radius_km=20).json()
    assert results[0]["full_name"] == "boosted"     # featured wins at equal distance


def test_category_filter(client):
    a1 = _artisan("plumb@x.com", 9.07, 7.49)
    a2 = _artisan("elec@x.com", 9.07, 7.49)
    plumbing = Category.objects.create(name="Plumbing")
    electrical = Category.objects.create(name="Electrical")
    Service.objects.create(artisan=a1.artisan_profile, category=plumbing,
                           title="Pipes", description="d", base_price=1000)
    Service.objects.create(artisan=a2.artisan_profile, category=electrical,
                           title="Wiring", description="d", base_price=1000)
    results = _match(client, radius_km=20, category=str(plumbing.id)).json()
    names = [r["full_name"] for r in results]
    assert names == ["plumb"]


def test_distance_reported(client):
    _artisan("near@x.com", 9.07, 7.49)
    results = _match(client, radius_km=20).json()
    assert results[0]["distance_km"] < 3       # ~1 km, sanity check
