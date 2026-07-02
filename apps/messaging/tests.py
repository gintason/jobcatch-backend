"""Phase 3D tests: REST conversations/messages + WebSocket broadcast."""
import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tokens import tokens_for_user
from apps.messaging.models import Conversation, Message

pytestmark = pytest.mark.django_db


def _user(email, role="customer"):
    return User.objects.create_user(email, "Str0ng!Pass9", full_name=email.split("@")[0],
                                    role=role, is_email_verified=True)


@pytest.fixture
def client():
    return APIClient()


# ---------------------------------------------------------------- REST
def test_create_conversation(client):
    a = _user("a@x.com")
    b = _user("b@x.com", "artisan")
    client.force_authenticate(a)
    resp = client.post("/api/v1/conversations/", {"participant": str(b.id)}, format="json")
    assert resp.status_code == 201
    assert set(resp.json()["participant_emails"]) == {"a@x.com", "b@x.com"}


def test_conversations_scoped_to_participants(client):
    a = _user("a2@x.com")
    b = _user("b2@x.com", "artisan")
    c = _user("c2@x.com")
    conv = Conversation.objects.create()
    conv.participants.add(a, b)
    client.force_authenticate(c)   # not a participant
    assert client.get("/api/v1/conversations/").json()["count"] == 0


def test_send_and_read_messages(client):
    a = _user("a3@x.com")
    b = _user("b3@x.com", "artisan")
    conv = Conversation.objects.create()
    conv.participants.add(a, b)

    client.force_authenticate(a)
    send = client.post(f"/api/v1/conversations/{conv.id}/messages/",
                       {"body": "Hello there"}, format="json")
    assert send.status_code == 201

    # b reads -> the message gets marked read
    client.force_authenticate(b)
    hist = client.get(f"/api/v1/conversations/{conv.id}/messages/")
    assert hist.json()["count"] == 1
    assert Message.objects.get(conversation=conv).read_at is not None


def test_non_participant_cannot_access_messages(client):
    a = _user("a4@x.com")
    b = _user("b4@x.com", "artisan")
    conv = Conversation.objects.create()
    conv.participants.add(a, b)
    intruder = _user("x@x.com")
    client.force_authenticate(intruder)
    assert client.get(f"/api/v1/conversations/{conv.id}/messages/").status_code == 404


# ---------------------------------------------------------------- WebSocket
@pytest.mark.django_db(transaction=True)
def test_websocket_message_broadcast(settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    from config.asgi import application

    sender = _user("ws-a@x.com")
    receiver = _user("ws-b@x.com", "artisan")
    conv = Conversation.objects.create()
    conv.participants.add(sender, receiver)
    _, access = tokens_for_user(sender)

    async def run():
        url = f"/ws/chat/{conv.id}/?token={access}"
        comm = WebsocketCommunicator(application, url)
        connected, _ = await comm.connect()
        assert connected, "socket should authenticate a participant"
        await comm.send_json_to({"body": "live message"})
        received = await comm.receive_json_from(timeout=5)
        assert received["body"] == "live message"
        assert received["sender_email"] == "ws-a@x.com"
        await comm.disconnect()

    async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_websocket_rejects_bad_token(settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    from config.asgi import application

    a = _user("ws-c@x.com")
    b = _user("ws-d@x.com", "artisan")
    conv = Conversation.objects.create()
    conv.participants.add(a, b)

    async def run():
        comm = WebsocketCommunicator(application, f"/ws/chat/{conv.id}/?token=garbage")
        connected, _ = await comm.connect()
        assert connected is False   # rejected at handshake
        await comm.disconnect()

    async_to_sync(run)()
