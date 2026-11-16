"""
Tool-calling functions for the assistant.

Every tool takes the authenticated `user` and scopes its query to that user's
own data — the same ownership rules as the REST API. The assistant can never
reach another user's records through a tool.
"""
import json

from django.db.models import Q

from apps.accounts.models import UserRole


def get_my_bookings(user, **kwargs):
    from apps.bookings.models import Booking

    qs = (Booking.objects
          .filter(Q(customer__user=user) | Q(artisan__user=user))
          .select_related("service")[:20])
    return [
        {"id": str(b.id), "status": b.status, "service": b.service.title,
         "scheduled_for": b.scheduled_for.isoformat()}
        for b in qs
    ]


def get_my_applications(user, **kwargs):
    from apps.jobs.models import Application

    if user.role == UserRole.EMPLOYER:
        qs = Application.objects.filter(job__employer__user=user)
    else:
        qs = Application.objects.filter(seeker__user=user)
    qs = qs.select_related("job")[:20]
    return [
        {"id": str(a.id), "job": a.job.title, "status": a.status}
        for a in qs
    ]


TOOLS = {
    "get_my_bookings": get_my_bookings,
    "get_my_applications": get_my_applications,
}

# OpenAI function-tool schemas.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_my_bookings",
        "description": "List the current user's bookings (as customer or artisan) with status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_my_applications",
        "description": "List the current user's job applications (seeker) or received applications (employer).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
]


def make_executor(user):
    """Return a callable(name, args) -> JSON string, scoped to `user`."""
    def _execute(name, args):
        fn = TOOLS.get(name)
        if not fn:
            return json.dumps({"error": f"unknown tool: {name}"})
        try:
            return json.dumps(fn(user, **(args or {})))
        except Exception as exc:  # noqa: BLE001 - never leak internals to the model
            return json.dumps({"error": str(exc)})
    return _execute
