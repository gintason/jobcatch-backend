"""
Conversations & message history (REST).

Live delivery is over WebSockets (see consumers.py); REST-sent messages are also
broadcast to the socket group so a mix of REST and WS clients stays in sync.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Conversation, Message
from .serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)


def _broadcast(conversation_id, message_dict):
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(
            f"chat_{conversation_id}", {"type": "chat.message", "message": message_dict}
        )


class ConversationViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return (Conversation.objects
                .filter(participants=self.request.user)
                .prefetch_related("participants")
                .distinct())

    def create(self, request, *args, **kwargs):
        ser = ConversationCreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        conv = Conversation.objects.create(
            booking=ser.validated_data.get("booking"),
            job=ser.validated_data.get("job"),
        )
        conv.participants.add(request.user, ser.validated_data["participant"])
        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        conv = self.get_object()  # 404 unless caller is a participant (queryset-scoped)

        if request.method == "POST":
            ser = MessageCreateSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            msg = ser.save(conversation=conv, sender=request.user)
            _broadcast(conv.id, {
                "id": str(msg.id), "sender_email": request.user.email,
                "body": msg.body, "created_at": msg.created_at.isoformat(),
            })
            return Response(MessageSerializer(msg).data, status=status.HTTP_201_CREATED)

        # GET: mark received-unread as read, then return history.
        conv.messages.filter(read_at__isnull=True).exclude(sender=request.user).update(
            read_at=timezone.now()
        )
        page = self.paginate_queryset(conv.messages.all())
        return self.get_paginated_response(MessageSerializer(page, many=True).data)
