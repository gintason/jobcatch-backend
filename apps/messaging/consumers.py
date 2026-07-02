"""WebSocket consumer for a single conversation."""
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        if (not self.user or isinstance(self.user, AnonymousUser)
                or not await self._is_participant()):
            await self.close(code=4001)
            return
        self.group = f"chat_{self.conversation_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return
        body = (data.get("body") or "").strip()
        if not body:
            return
        message = await self._save(body)
        await self.channel_layer.group_send(
            self.group, {"type": "chat.message", "message": message}
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    # --- DB helpers ---
    @database_sync_to_async
    def _is_participant(self):
        from .models import Conversation
        return Conversation.objects.filter(
            id=self.conversation_id, participants=self.user
        ).exists()

    @database_sync_to_async
    def _save(self, body):
        from .models import Conversation, Message
        conv = Conversation.objects.get(id=self.conversation_id)
        msg = Message.objects.create(conversation=conv, sender=self.user, body=body)
        return {
            "id": str(msg.id),
            "sender_email": self.user.email,
            "body": msg.body,
            "created_at": msg.created_at.isoformat(),
        }
