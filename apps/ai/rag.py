"""Retrieval: embed the query, cosine-rank KB chunks, return the top-k as context.

Swap this function's body for a pgvector query later; the interface stays the same.
"""
import math

from django.conf import settings

from .providers import get_provider


def _cosine(a, b):
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve_context(query, k=None):
    from .models import KBChunk

    chunks = list(KBChunk.objects.exclude(embedding=[]))
    if not chunks:
        return ""
    try:
        qvec = get_provider().embed([query])[0]
    except Exception:  # noqa: BLE001 - retrieval is best-effort
        return ""
    ranked = sorted(chunks, key=lambda c: _cosine(qvec, c.embedding), reverse=True)
    top = ranked[: (k or settings.AI_KB_TOP_K)]
    return "\n\n".join(f"{c.title}: {c.content}" for c in top)
