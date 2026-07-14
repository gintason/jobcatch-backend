"""
Liveness endpoint for the platform's load balancer.

Deliberately unauthenticated, unthrottled, and free of database work: a health
check must answer "is this process up?" and nothing else. Pointing the checker at
a real API route means a throttle or a slow query can take a healthy instance
down.
"""
from django.http import JsonResponse


def healthz(request):
    return JsonResponse({"status": "ok"})
