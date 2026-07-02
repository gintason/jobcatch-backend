"""Chat orchestration: retrieve context -> build prompt -> generate (with tools)."""
from .models import AIConversation, AIMessage
from .providers import get_provider
from .rag import retrieve_context
from .tools import TOOL_SCHEMAS, make_executor

SYSTEM_BASE = (
    "You are JobCatch's helpful assistant. Answer using the knowledge base and the "
    "user's own data (via tools). Be concise. If you don't know, say so and suggest "
    "contacting support. Never reveal information about other users."
)


def _system_prompt(context):
    return f"{SYSTEM_BASE}\n\nKnowledge base:\n{context}" if context else SYSTEM_BASE


def chat(user, message, conversation=None):
    conversation = conversation or AIConversation.objects.create(user=user)

    history = [
        {"role": m.role, "content": m.content}
        for m in conversation.messages.all()
    ]
    history.append({"role": "user", "content": message})

    context = retrieve_context(message)
    provider = get_provider()
    executor = make_executor(user)

    try:
        reply = provider.generate(
            system=_system_prompt(context),
            messages=history,
            tools=TOOL_SCHEMAS,
            tool_executor=executor,
        )
    except Exception:  # noqa: BLE001 - degrade gracefully, never 500 the chat
        reply = "Sorry, the assistant is temporarily unavailable. Please try again shortly."

    AIMessage.objects.create(conversation=conversation, role="user", content=message)
    AIMessage.objects.create(conversation=conversation, role="assistant", content=reply)
    return conversation, reply
