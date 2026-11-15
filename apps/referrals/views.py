from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class ReferralSummaryView(APIView):
    """GET /referrals/ — my code, link, rewards and the people I invited."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.summary(request.user))


class ApplyReferralView(APIView):
    """POST /referrals/apply/ {code} — add an inviter's code shortly after joining."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        services.attach(request.user, str(request.data.get("code") or ""), late=True)
        return Response(services.summary(request.user))
