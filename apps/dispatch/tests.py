"""Dispatch & delivery: pricing, rider onboarding, matching, split payment, lifecycle."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.dispatch.models import Delivery, RiderProfile
from apps.dispatch.services import expire_stale_requests
from apps.payments.models import Payment
from apps.payments.services import confirm_payment

pytestmark = pytest.mark.django_db

# Lekki Phase 1 -> Victoria Island (≈ 6 km straight line)
LEKKI = {"lat": 6.4474, "lng": 3.4730}
VI = {"lat": 6.4281, "lng": 3.4219}


@pytest.fixture(autouse=True)
def _settings(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    settings.PAYSTACK_SECRET_KEY = "sk_test_x"


@pytest.fixture
def client():
    return APIClient()


def _user(email, **kw):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0].title(),
                                    role=kw.pop("role", "customer"), is_email_verified=True, **kw)


@pytest.fixture
def customer():
    return _user("ada@x.com", phone="08030000001")


class FakePaystack:
    def __init__(self):
        self.split = None

    def is_configured(self):
        return True

    def list_banks(self):
        return [{"name": "Access Bank", "code": "044"}]

    def resolve_account(self, *, account_number, bank_code):
        return "MUSA IBRAHIM"

    def create_subaccount(self, **kw):
        self.subaccount_kw = kw
        return "ACCT_rider1"

    def initialize(self, *, reference, amount, email, callback_url, name="", description="", split=None):
        self.split = split
        return {"authorization_url": "https://checkout.paystack.com/x", "reference": reference}


@pytest.fixture
def paystack():
    fake = FakePaystack()
    with patch("apps.dispatch.services.get_gateway", return_value=fake), \
            patch("apps.payments.services.get_gateway", return_value=fake):
        yield fake


def _approved_rider(client, paystack, email="musa@x.com", at=LEKKI):
    user = _user(email)
    client.force_authenticate(user)
    doc = SimpleUploadedFile("id.png", b"\x89PNG\r\n\x1a\nid", content_type="image/png")
    selfie = SimpleUploadedFile("me.jpg", b"\xff\xd8\xffselfie", content_type="image/jpeg")
    r = client.patch("/api/v1/dispatch/rider/", {"phone": "08031112222", "vehicle_type": "bike",
                                                 "plate_number": "LND-123AB", "id_type": "nin",
                                                 "id_number": "12345678901", "id_document": doc, "selfie": selfie},
                     format="multipart")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "incomplete"               # no payout account yet
    assert r.json()["missing"] == ["payout account"] and r.json()["has_selfie"]
    r = client.post("/api/v1/dispatch/rider/payout-account/",
                    {"bank_code": "044", "bank_name": "Access Bank", "account_number": "0123456789"},
                    format="json")
    assert r.json()["status"] == "pending" and r.json()["account_name"] == "MUSA IBRAHIM"
    assert paystack.subaccount_kw["percentage_charge"] == 20.0
    admin = User.objects.filter(email="admin@x.com").first() or User.objects.create_superuser(
        "admin@x.com", "Str0ng!Pass9", full_name="Admin", role="admin", is_staff=True)
    rider = RiderProfile.objects.get(user=user)
    client.force_authenticate(admin)
    assert client.post(f"/api/v1/admin/riders/{rider.id}/approve/").json()["status"] == "approved"
    client.force_authenticate(user)
    assert client.post("/api/v1/dispatch/rider/availability/",
                       {"online": True, **at}, format="json").json()["is_online"] is True
    return user


def _book(client, customer, kind="package", **extra):
    client.force_authenticate(customer)
    return client.post("/api/v1/dispatch/deliveries/", {
        "kind": kind, "vehicle_type": "bike",
        "pickup_lat": LEKKI["lat"], "pickup_lng": LEKKI["lng"], "pickup_address": "Admiralty Way, Lekki",
        "dropoff_lat": VI["lat"], "dropoff_lng": VI["lng"], "dropoff_address": "Adeola Odeku, VI",
        "recipient_name": "Tunde", "recipient_phone": "0802 222 3333",
        "item_description": "Small parcel", **extra}, format="json")


def test_quote_is_distance_based_and_rounded(client):
    r = client.post("/api/v1/dispatch/quote/", {
        "pickup_lat": LEKKI["lat"], "pickup_lng": LEKKI["lng"], "dropoff_lat": VI["lat"],
        "dropoff_lng": VI["lng"], "vehicle_type": "bike"}, format="json").json()
    km = Decimal(r["distance_km"])
    assert Decimal("7") < km < Decimal("9")                  # ~6 km × road factor 1.3
    fare = Decimal(r["fare"])
    assert fare % 50 == 0 and fare >= Decimal("1200")
    assert Decimal(r["rider_share"]) == fare * Decimal("0.8")
    errand = client.post("/api/v1/dispatch/quote/", {
        "pickup_lat": LEKKI["lat"], "pickup_lng": LEKKI["lng"], "dropoff_lat": VI["lat"],
        "dropoff_lng": VI["lng"], "vehicle_type": "bike", "kind": "errand"}, format="json").json()
    assert Decimal(errand["fare"]) == fare + 500


def test_full_delivery_with_split_payment(client, customer, paystack, django_capture_on_commit_callbacks):
    rider_user = _approved_rider(client, paystack)
    with django_capture_on_commit_callbacks(execute=True):
        booked = _book(client, customer)
    assert booked.status_code == 201, booked.json()
    d = booked.json()
    assert d["status"] == "searching" and len(d["delivery_code"]) == 4

    # The rider sees the request nearby, without the customer's details.
    client.force_authenticate(rider_user)
    open_ = client.get("/api/v1/dispatch/rider/requests/").json()
    assert [o["id"] for o in open_] == [d["id"]]
    assert "recipient_phone" not in open_[0] and "delivery_code" not in open_[0]
    job = client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/accept/").json()
    assert job["status"] == "accepted" and job["delivery_code"] is None   # code stays with the customer
    assert job["customer_phone"] == "08030000001"

    # Can't pay before pickup.
    client.force_authenticate(customer)
    assert client.post(f"/api/v1/dispatch/deliveries/{d['id']}/pay/").status_code == 400
    client.force_authenticate(rider_user)
    assert client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/picked-up/").json()["status"] == "picked_up"

    client.force_authenticate(customer)
    pay = client.post(f"/api/v1/dispatch/deliveries/{d['id']}/pay/")
    assert pay.json()["authorization_url"].startswith("https://checkout.paystack.com")
    fare = Decimal(d["fare"])
    assert paystack.split == {"subaccount": "ACCT_rider1", "platform_amount": fare * Decimal("0.2"),
                              "bearer": "account"}
    payment = Payment.objects.get(purpose="delivery")
    assert payment.gateway == "paystack" and payment.commission == fare * Decimal("0.2")
    confirm_payment(payment.reference, source="test", paid_amount=payment.amount)
    assert Delivery.objects.get(pk=d["id"]).status == "in_transit"

    client.force_authenticate(rider_user)
    assert client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/deliver/", {"code": "0000" if d["delivery_code"] != "0000" else "1111"},
                       format="json").status_code == 400
    done = client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/deliver/", {"code": d["delivery_code"]},
                       format="json").json()
    assert done["status"] == "delivered"
    earnings = client.get("/api/v1/dispatch/rider/jobs/").json()["earnings"]
    assert Decimal(earnings["total"]) == fare * Decimal("0.8") and earnings["count"] == 1

    client.force_authenticate(customer)
    rated = client.post(f"/api/v1/dispatch/deliveries/{d['id']}/rate/", {"rating": 5}, format="json")
    assert rated.json()["rating"] == 5
    assert RiderProfile.objects.get(user=rider_user).rating == 5.0


def test_only_one_rider_can_accept(client, customer, paystack):
    r1 = _approved_rider(client, paystack, "r1@x.com")
    r2 = _approved_rider(client, paystack, "r2@x.com")
    d = _book(client, customer).json()
    client.force_authenticate(r1)
    assert client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/accept/").status_code == 200
    client.force_authenticate(r2)
    assert client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/accept/").status_code == 400
    assert client.get("/api/v1/dispatch/rider/requests/").json() == []


def test_far_riders_and_unapproved_riders_see_nothing(client, customer, paystack):
    far = _approved_rider(client, paystack, "far@x.com", at={"lat": 7.3775, "lng": 3.9470})  # Ibadan
    _book(client, customer)
    client.force_authenticate(far)
    assert client.get("/api/v1/dispatch/rider/requests/").json() == []
    newbie = _user("new@x.com")
    client.force_authenticate(newbie)
    assert client.post("/api/v1/dispatch/rider/availability/", {"online": True, **LEKKI},
                       format="json").status_code == 400


def test_customer_can_cancel_before_pickup_only(client, customer, paystack):
    rider = _approved_rider(client, paystack)
    d = _book(client, customer).json()
    client.force_authenticate(rider)
    client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/accept/")
    client.post(f"/api/v1/dispatch/rider/jobs/{d['id']}/picked-up/")
    client.force_authenticate(customer)
    assert client.post(f"/api/v1/dispatch/deliveries/{d['id']}/cancel/").status_code == 400
    d2 = _book(client, customer).json()
    assert client.post(f"/api/v1/dispatch/deliveries/{d2['id']}/cancel/").json()["status"] == "cancelled"


def test_food_pickup_needs_vendor_and_paid_order(client, customer):
    # Riders never buy items: the customer orders and pays the vendor, the rider only collects.
    r = _book(client, customer, kind="errand", item_description="2 packs of jollof rice")
    assert r.status_code == 400
    errors = r.json()
    assert {"pickup_contact_name", "pickup_contact_phone", "order_paid"} <= set(errors)

    not_paid = _book(client, customer, kind="errand", item_description="2 packs of jollof rice",
                     pickup_contact_name="Mama Put", pickup_contact_phone="0803 111 2222")
    assert not_paid.status_code == 400 and "order_paid" in not_paid.json()

    ok = _book(client, customer, kind="errand", item_description="2 packs of jollof rice",
               errand_details="Order #A102, under the name Tunde",
               pickup_contact_name="Mama Put", pickup_contact_phone="0803 111 2222",
               order_paid=True, errand_budget="6000")
    assert ok.status_code == 201, ok.json()
    d = Delivery.objects.get(pk=ok.json()["id"])
    assert d.errand_budget is None            # riders are never given money to spend
    assert d.get_kind_display() == "Food & essentials pickup"


def test_stale_requests_expire(client, customer):
    d = _book(client, customer).json()
    Delivery.objects.filter(pk=d["id"]).update(created_at=timezone.now() - timedelta(hours=2))
    assert expire_stale_requests() == 1
    assert Delivery.objects.get(pk=d["id"]).status == "expired"


def test_strangers_cannot_see_a_delivery(client, customer):
    d = _book(client, customer).json()
    client.force_authenticate(_user("nosy@x.com"))
    assert client.get(f"/api/v1/dispatch/deliveries/{d['id']}/").status_code == 404


@override_settings(PAYSTACK_SECRET_KEY="")
def test_payout_account_needs_paystack(client):
    client.force_authenticate(_user("r@x.com"))
    r = client.post("/api/v1/dispatch/rider/payout-account/",
                    {"bank_code": "044", "account_number": "0123456789"}, format="json")
    assert r.status_code == 400


def test_paystack_split_request_body():
    from apps.payments.gateways import PaystackGateway

    with patch("apps.payments.gateways._post_json",
               return_value={"data": {"authorization_url": "u", "reference": "r"}}) as post:
        PaystackGateway().initialize(reference="JC-1", amount=Decimal("2500"), email="a@x.com",
                                     callback_url="https://cb", split={
                                         "subaccount": "ACCT_x", "platform_amount": Decimal("500")})
    body = post.call_args[0][1]
    assert body["amount"] == 250000 and body["subaccount"] == "ACCT_x"
    assert body["transaction_charge"] == 50000 and body["bearer"] == "account"



def test_rider_without_selfie_is_not_sent_for_review(client, paystack):
    user = _user("noselfie@x.com")
    client.force_authenticate(user)
    doc = SimpleUploadedFile("id.png", b"\x89PNG\r\n\x1a\nid", content_type="image/png")
    client.patch("/api/v1/dispatch/rider/", {"phone": "08031112222", "id_type": "drivers_licence",
                                             "id_number": "ABC12345678", "id_document": doc}, format="multipart")
    r = client.post("/api/v1/dispatch/rider/payout-account/",
                    {"bank_code": "044", "bank_name": "Access Bank", "account_number": "0123456789"}, format="json")
    assert r.json()["status"] == "incomplete" and r.json()["missing"] == ["selfie"]
    bad = SimpleUploadedFile("me.pdf", b"%PDF", content_type="application/pdf")
    assert client.patch("/api/v1/dispatch/rider/", {"selfie": bad}, format="multipart").status_code == 400
    ok = SimpleUploadedFile("me.jpg", b"\xff\xd8\xffselfie", content_type="image/jpeg")
    r = client.patch("/api/v1/dispatch/rider/", {"selfie": ok}, format="multipart")
    assert r.json()["status"] == "pending" and r.json()["missing"] == []
    admin = User.objects.create_superuser("admin2@x.com", "Str0ng!Pass9", full_name="Admin", role="admin", is_staff=True)
    client.force_authenticate(admin)
    rider = RiderProfile.objects.get(user=user)
    assert client.get(f"/api/v1/admin/riders/{rider.id}/selfie/").status_code == 200
    client.force_authenticate(user)
    assert client.get(f"/api/v1/admin/riders/{rider.id}/selfie/").status_code == 403     # riders can't fetch it
