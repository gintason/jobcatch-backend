"""AI chatbot endpoints."""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import StreamingHttpResponse

from .models import AIConversation
from .services import chat
from .providers import get_provider


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"detail": "message is required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Get or create conversation
        conversation = None
        session_id = request.data.get("session_id")
        if session_id:
            conversation = AIConversation.objects.filter(
                id=session_id, user=request.user).first()

        # We'll use the same chat logic but return a stream.
        # We need to adapt the chat function to return the provider and parameters.
        # To avoid rewriting everything, we'll call a new function that yields tokens.
        def stream_generator():
            # Replicate the logic from services.chat but in a streaming fashion.
            conversation_obj = conversation or AIConversation.objects.create(user=request.user)
            history = [
                {"role": m.role, "content": m.content}
                for m in conversation_obj.messages.all()
            ]
            history.append({"role": "user", "content": message})

            context = retrieve_context(message)  # from rag
            provider = get_provider()
            executor = make_executor(request.user)  # from tools

            # Save user message immediately
            from .models import AIMessage
            AIMessage.objects.create(conversation=conversation_obj, role="user", content=message)

            # Stream assistant reply
            full_reply = ""
            try:
                for chunk in provider.generate_stream(
                    system=_system_prompt(context),  # from services
                    messages=history,
                    tools=TOOL_SCHEMAS,
                    tool_executor=executor,
                ):
                    full_reply += chunk
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
            except Exception as e:
                # On error, send an error chunk
                error_msg = "Sorry, the assistant is temporarily unavailable. Please try again shortly."
                yield f"data: {json.dumps({'content': error_msg})}\n\n"
                full_reply = error_msg

            # Save assistant message after streaming
            AIMessage.objects.create(conversation=conversation_obj, role="assistant", content=full_reply)

            # Finally send session_id
            yield f"data: {json.dumps({'session_id': str(conversation_obj.id)})}\n\n"

        response = StreamingHttpResponse(stream_generator(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        return response


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
