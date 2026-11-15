from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import services
from .models import BankTransfer


class LookupThrottle(UserRateThrottle):
    scope = "transfer_lookup"           # account-name lookups are enumerable — keep them slow


class OverviewView(APIView):
    """GET /transfers/overview/ — PIN status, balance, limits, saved bank accounts."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.overview(request.user))


class PinView(APIView):
    """POST /transfers/pin/ {pin, current_pin? | password?} — create, change or reset the PIN."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        services.set_pin(request.user, pin=request.data.get("pin"), current_pin=request.data.get("current_pin"),
                         password=request.data.get("password"))
        return Response(services.overview(request.user))


class LookupView(APIView):
    """GET /transfers/lookup/?to=email-or-phone — confirm who you're paying."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [LookupThrottle]

    def get(self, request):
        user = services.find_recipient(request.query_params.get("to"))
        if user is None or user.pk == request.user.pk:
            return Response({"detail": "No JobCatch account uses that email or phone number."
                             if user is None else "You can't send money to yourself."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"name": user.full_name, "email": services.mask_email(user.email)})


class WalletTransferView(APIView):
    """POST /transfers/wallet/ {to, amount, pin, note?}"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = request.data
        t = services.send_to_wallet(request.user, to=d.get("to"), amount=d.get("amount"), pin=d.get("pin"),
                                    note=d.get("note") or "")
        return Response({"id": str(t.pk), "type": "wallet_out", "reference": t.reference,
                         "counterparty": t.recipient.full_name, "detail": "JobCatch wallet", "amount": str(t.amount),
                         "fee": "0.00", "status": "successful", "status_label": "Successful", "note": t.note,
                         "created_at": t.created_at.isoformat()}, status=status.HTTP_201_CREATED)


class BanksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.banks())


class ResolveView(APIView):
    """POST /transfers/resolve/ {bank_code, account_number} → {account_name}"""
    permission_classes = [IsAuthenticated]
    throttle_classes = [LookupThrottle]

    def post(self, request):
        return Response({"account_name": services.resolve(str(request.data.get("bank_code") or ""),
                                                           str(request.data.get("account_number") or ""))})


class BankTransferView(APIView):
    """POST /transfers/bank/ {bank_code, account_number, amount, pin, narration?}"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = request.data
        bt = services.send_to_bank(request.user, bank_code=str(d.get("bank_code") or ""),
                                   account_number=str(d.get("account_number") or ""), amount=d.get("amount"),
                                   pin=d.get("pin"), narration=d.get("narration") or "")
        bt.refresh_from_db()
        return Response(services.bank_row(bt), status=status.HTTP_201_CREATED)


class BankTransferDetailView(APIView):
    """GET /transfers/bank/<id>/ — status of one withdrawal (re-checks Paystack while processing)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        bt = BankTransfer.objects.filter(pk=pk, user=request.user).first()
        if bt is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # While it's processing, ask Paystack at most every 15 s (the webhook usually wins).
        if bt.status == "processing" and cache.add(f"bt-recheck:{bt.pk}", 1, 15):
            bt = services.requery(bt)
            bt.refresh_from_db()
        return Response(services.bank_row(bt))


class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.history(request.user))
