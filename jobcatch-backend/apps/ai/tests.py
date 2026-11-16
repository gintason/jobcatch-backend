"""Phase 3E tests: AI chatbot (mock provider, offline)."""
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai.models import AIConversation, KBChunk
from apps.ai.providers import MockProvider
from apps.ai.rag import retrieve_context
from apps.ai.tools import get_my_bookings
from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import Category, Service

pytestmark = pytest.mark.django_db


def _user(email, role="customer"):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0],
                                    role=role, is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


def _booking(customer, artisan, status=BookingStatus.PENDING):
    cat = Category.objects.create(name="Plumbing")
    service = Service.objects.create(artisan=artisan.artisan_profile, category=cat,
                                     title="Pipe", description="d", base_price=5000)
    return Booking.objects.create(
        customer=customer.customer_profile, artisan=artisan.artisan_profile, service=service,
        scheduled_for=timezone.now() + timedelta(days=1), location=Point(7.49, 9.06),
        address="Abuja", agreed_price=5000, status=status,
    )


def test_auth_required(client):
    assert client.post("/api/v1/ai/chat/", {"message": "hi"}, format="json").status_code == 401


def test_chat_returns_reply(client):
    user = _user("u@x.com")
    client.force_authenticate(user)
    resp = client.post("/api/v1/ai/chat/", {"message": "hello"}, format="json")
    assert resp.status_code == 200
    assert "reply" in resp.json() and "session_id" in resp.json()
    assert AIConversation.objects.filter(user=user).count() == 1


def test_chat_uses_booking_tool(client):
    customer = _user("cust@x.com")
    artisan = _user("art@x.com", "artisan")
    _booking(customer, artisan, status=BookingStatus.ACCEPTED)
    client.force_authenticate(customer)
    resp = client.post("/api/v1/ai/chat/",
                       {"message": "what is the status of my booking?"}, format="json")
    assert resp.status_code == 200
    # Mock provider routes booking queries through the scoped tool.
    assert "accepted" in resp.json()["reply"]


def test_tool_scoped_to_user():
    c1 = _user("c1@x.com")
    c2 = _user("c2@x.com")
    artisan = _user("art2@x.com", "artisan")
    _booking(c1, artisan)
    # c2 has no bookings -> the tool returns nothing for them
    assert get_my_bookings(c2) == []
    assert len(get_my_bookings(c1)) == 1


def test_multi_turn_keeps_history(client):
    user = _user("u2@x.com")
    client.force_authenticate(user)
    first = client.post("/api/v1/ai/chat/", {"message": "hello"}, format="json").json()
    sid = first["session_id"]
    client.post("/api/v1/ai/chat/",
                {"message": "again", "session_id": sid}, format="json")
    hist = client.get(f"/api/v1/ai/history/?session_id={sid}")
    assert hist.status_code == 200
    # 2 user + 2 assistant messages
    assert len(hist.json()) == 4


def test_rag_retrieval_ranks_relevant_chunk():
    provider = MockProvider()
    KBChunk.objects.create(title="Payments",
                           content="JobCatch supports card payments via Paystack.",
                           embedding=provider.embed(["JobCatch supports card payments via Paystack."])[0])
    KBChunk.objects.create(title="Weather",
                           content="The sky is blue and rain falls sometimes.",
                           embedding=provider.embed(["The sky is blue and rain falls sometimes."])[0])
    context = retrieve_context("How do payments and Paystack work?", k=1)
    assert "Paystack" in context


def test_history_scoped_to_owner(client):
    owner = _user("owner@x.com")
    conv = AIConversation.objects.create(user=owner)
    intruder = _user("intruder@x.com")
    client.force_authenticate(intruder)
    assert client.get(f"/api/v1/ai/history/?session_id={conv.id}").status_code == 404
