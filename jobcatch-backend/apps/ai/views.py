"""AI chatbot endpoints.

AIChatView streams the assistant's reply as Server-Sent Events (SSE). Each SSE
line is a JSON object: {"content": "<token>"} for reply chunks, then a final
{"session_id": "<uuid>"} so the client can continue the conversation.

The whole generator is wrapped so a provider failure degrades to a friendly
message instead of a 500 — the chat must never hard-fail the UI.
"""
import json

from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIConversation, AIMessage
from .providers import get_provider
from .rag import retrieve_context
from .services import _system_prompt
from .tools import TOOL_SCHEMAS, make_executor


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response(
                {"detail": "message is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve the conversation up front (outside the generator) so an invalid
        # session_id can't be silently swallowed by the streaming response.
        conversation = None
        session_id = request.data.get("session_id")
        if session_id:
            conversation = AIConversation.objects.filter(
                id=session_id, user=request.user
            ).first()
        conversation = conversation or AIConversation.objects.create(user=request.user)

        # Build history from persisted turns, then append the new user message.
        history = [
            {"role": m.role, "content": m.content}
            for m in conversation.messages.all()
        ]
        history.append({"role": "user", "content": message})

        # Persist the user turn immediately so it survives a mid-stream disconnect.
        AIMessage.objects.create(conversation=conversation, role="user", content=message)

        context = retrieve_context(message)
        provider = get_provider()
        executor = make_executor(request.user)
        system = _system_prompt(context)

        def stream_generator():
            full_reply = ""
            try:
                for chunk in provider.generate_stream(
                    system=system,
                    messages=history,
                    tools=TOOL_SCHEMAS,
                    tool_executor=executor,
                ):
                    if not chunk:
                        continue
                    full_reply += chunk
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
            except Exception:  # noqa: BLE001 - never hard-fail the chat UI
                fallback = (
                    "Sorry, the assistant is temporarily unavailable. "
                    "Please try again shortly."
                )
                if not full_reply:
                    full_reply = fallback
                    yield f"data: {json.dumps({'content': fallback})}\n\n"

            AIMessage.objects.create(
                conversation=conversation, role="assistant", content=full_reply
            )
            yield f"data: {json.dumps({'session_id': str(conversation.id)})}\n\n"

        response = StreamingHttpResponse(
            stream_generator(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # disable nginx buffering so tokens flush
        return response


class AIHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        session_id = request.query_params.get("session_id")
        conversation = AIConversation.objects.filter(
            id=session_id, user=request.user
        ).first()
        if not conversation:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        data = [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in conversation.messages.all()
        ]
        return Response(data)
