"""AI chatbot endpoints."""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIConversation
from .services import chat


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"detail": "message is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        conversation = None
        session_id = request.data.get("session_id")
        if session_id:
            conversation = AIConversation.objects.filter(
                id=session_id, user=request.user).first()
        conversation, reply = chat(request.user, message, conversation)
        return Response({"session_id": str(conversation.id), "reply": reply})


class AIHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        session_id = request.query_params.get("session_id")
        conversation = AIConversation.objects.filter(
            id=session_id, user=request.user).first()
        if not conversation:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        data = [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in conversation.messages.all()
        ]
        return Response(data)
