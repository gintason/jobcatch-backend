"""
Engagement API.

  GET  /engagement/prompts/?audience=job_seeker|artisan|employer
  POST /engagement/prompts/<key>/<seen|dismiss|click>/
  GET  /engagement/seeker-pro/               -> status, price, features
  POST /engagement/seeker-pro/checkout/      -> {gateway} -> checkout URL
  GET/PUT /engagement/job-alerts/            -> WhatsApp alert preferences
"""
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.services import CheckoutError

from .models import Audience, JobAlertSubscription, UpgradePrompt
from .serializers import CheckoutSerializer, JobAlertSubscriptionSerializer, UpgradePromptSerializer
from .services import (
    SEEKER_PRO_FEATURES, active_seeker_pro, prompts_for, record_prompt_event,
    start_seeker_pro_checkout,
)


class PromptListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        audience = request.query_params.get("audience")
        if audience not in Audience.values:
            return Response({"audience": "Use job_seeker, artisan or employer."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(UpgradePromptSerializer(prompts_for(request.user, audience), many=True).data)


class PromptEventView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, key, event):
        if event not in ("seen", "dismiss", "click"):
            return Response({"detail": "Unknown event."}, status=status.HTTP_400_BAD_REQUEST)
        prompt = get_object_or_404(UpgradePrompt, key=key)
        record_prompt_event(request.user, prompt, event)
        return Response({"ok": True})


class SeekerProView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = active_seeker_pro(request.user)
        return Response({
            "is_pro": bool(sub),
            "expires_at": sub.expires_at if sub else None,
            "price": str(settings.SEEKER_PRO_PRICE),
            "period_days": settings.SEEKER_PRO_PERIOD_DAYS,
            "features": SEEKER_PRO_FEATURES,
        })


class SeekerProCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            payment, url = start_seeker_pro_checkout(user=request.user,
                                                     gateway=ser.validated_data.get("gateway"))
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"authorization_url": url, "reference": payment.reference},
                        status=status.HTTP_201_CREATED)


class JobAlertView(APIView):
    permission_classes = [IsAuthenticated]

    def _sub(self, request):
        sub, _ = JobAlertSubscription.objects.get_or_create(
            user=request.user,
            defaults={"whatsapp_number": request.user.phone or ""},
        )
        return sub

    def get(self, request):
        sub = self._sub(request)
        data = JobAlertSubscriptionSerializer(sub).data
        data["is_pro"] = bool(active_seeker_pro(request.user))
        return Response(data)

    def put(self, request):
        sub = self._sub(request)
        ser = JobAlertSubscriptionSerializer(sub, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        data = ser.data
        data["is_pro"] = bool(active_seeker_pro(request.user))
        return Response(data)
