"""Helpers for writing to and reading from the append-only AuditLog."""
from .models import AuditLog


def _entity_label(obj):
    return f"{obj._meta.app_label}.{obj.__class__.__name__}"


def _client_ip(request):
    if not request:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


def record(obj, action, *, actor=None, request=None, **metadata):
    actor = actor or (getattr(request, "user", None) if request else None)
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        entity=_entity_label(obj),
        entity_id=str(obj.pk),
        metadata=metadata,
        ip_address=_client_ip(request),
    )


def history(obj):
    """Every audit entry for one object, oldest first."""
    return (AuditLog.objects
            .filter(entity=_entity_label(obj), entity_id=str(obj.pk))
            .select_related("actor")
            .order_by("created_at"))
