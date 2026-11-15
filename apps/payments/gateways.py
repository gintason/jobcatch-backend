"""
Payment gateway abstraction.

A thin interface so providers are just subclasses — the rest of the app only
ever talks to `get_gateway(name)`. Uses the standard library for HTTP so no
extra dependency is required.

Every gateway's `verify()` returns the *amount actually paid* so the caller can
refuse to deliver value when a transaction succeeded for the wrong amount.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from rest_framework.exceptions import ValidationError


# Paystack sits behind a WAF that rejects the default "Python-urllib/x.y"
# agent with 403, so every request identifies itself properly.
_BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "JobCatch/1.0 (+https://jobcatchonline.com)",
}


class GatewayError(Exception):
    """
    Raised when the payment provider rejects or fails a request. `http_status` is
    the provider's HTTP status when it answered (a definite rejection), or None
    when we couldn't reach it (outcome unknown — check again before retrying).
    """

    def __init__(self, message="", http_status=None):
        super().__init__(message)
        self.http_status = http_status


def _request_json(url, headers, payload=None, method="GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={**_BASE_HEADERS, **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500]
        raise GatewayError(f"Payment provider returned {exc.code}: {body}", http_status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise GatewayError(f"Could not reach payment provider: {exc.reason}") from exc


def _post_json(url, payload, headers):
    return _request_json(url, headers, payload=payload, method="POST")


def _get_json(url, headers):
    return _request_json(url, headers, method="GET")


class PaymentGateway:
    name = None

    def is_configured(self) -> bool:
        raise NotImplementedError

    def initialize(self, *, reference, amount, email, callback_url, name="", description="") -> dict:
        """Return {"authorization_url", "reference"}. `amount` is Naira (Decimal)."""
        raise NotImplementedError

    def verify(self, reference) -> dict:
        """Return {"status": "success"|"failed"|"pending", "amount": Decimal|None, "currency", "raw"}."""
        raise NotImplementedError

    def verify_webhook(self, request) -> bool:
        raise NotImplementedError

    def parse_event(self, payload: dict):
        """Return (reference, status, amount|None)."""
        raise NotImplementedError


class PaystackGateway(PaymentGateway):
    name = "paystack"
    BASE = "https://api.paystack.co"

    def __init__(self):
        # Overridable only for local end-to-end testing against a stub.
        self.BASE = getattr(settings, "PAYSTACK_BASE_URL", "") or self.BASE

    def is_configured(self):
        return bool(settings.PAYSTACK_SECRET_KEY)

    @property
    def _auth(self):
        return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}

    def initialize(self, *, reference, amount, email, callback_url, name="", description="",
                   split=None):
        resp = _post_json(
            f"{self.BASE}/transaction/initialize",
            {"email": email, "amount": int(Decimal(amount) * 100),
             "reference": reference, "callback_url": callback_url,
             "metadata": {"description": description},
             # Split payments: Paystack settles the subaccount's share straight to
             # its bank account and `transaction_charge` (kobo) to ours.
             **({"subaccount": split["subaccount"],
                 "transaction_charge": int(Decimal(split["platform_amount"]) * 100),
                 "bearer": split.get("bearer", "account")} if split else {})},
            self._auth,
        )
        data = resp.get("data", {})
        return {"authorization_url": data.get("authorization_url"),
                "reference": data.get("reference", reference)}

    # ---- split payments (riders' payout accounts) ----
    def list_banks(self):
        resp = _get_json(f"{self.BASE}/bank?country=nigeria&perPage=200", self._auth)
        return [{"name": b["name"], "code": b["code"]} for b in resp.get("data") or []
                if b.get("active", True) and b.get("code")]

    def resolve_account(self, *, account_number, bank_code):
        """The registered account name, or GatewayError if the account doesn't exist."""
        resp = _get_json(f"{self.BASE}/bank/resolve?account_number="
                         f"{urllib.parse.quote(account_number)}&bank_code={urllib.parse.quote(bank_code)}",
                         self._auth)
        name = (resp.get("data") or {}).get("account_name")
        if not resp.get("status") or not name:
            raise GatewayError(resp.get("message") or "Account not found")
        return name

    def create_subaccount(self, *, business_name, bank_code, account_number, percentage_charge,
                          description=""):
        resp = _post_json(f"{self.BASE}/subaccount", {
            "business_name": business_name[:100], "settlement_bank": bank_code,
            "account_number": account_number, "percentage_charge": float(percentage_charge),
            "description": description[:200]}, self._auth)
        code = (resp.get("data") or {}).get("subaccount_code")
        if not resp.get("status") or not code:
            raise GatewayError(resp.get("message") or "Could not create the payout account")
        return code

    # ---- transfers (wallet withdrawals to bank) ----
    def create_transfer_recipient(self, *, name, account_number, bank_code):
        resp = _post_json(f"{self.BASE}/transferrecipient", {
            "type": "nuban", "name": name[:100], "account_number": account_number,
            "bank_code": bank_code, "currency": "NGN"}, self._auth)
        code = (resp.get("data") or {}).get("recipient_code")
        if not resp.get("status") or not code:
            raise GatewayError(resp.get("message") or "Could not add the recipient", http_status=400)
        return code

    def initiate_transfer(self, *, amount, recipient_code, reference, reason=""):
        """{status: success|pending|otp|failed|..., transfer_code, raw}."""
        resp = _post_json(f"{self.BASE}/transfer", {
            "source": "balance", "amount": int((Decimal(amount) * 100).to_integral_value()),
            "recipient": recipient_code, "reference": reference, "reason": reason[:100],
            "currency": "NGN"}, self._auth)
        data = resp.get("data") or {}
        return {"status": str(data.get("status") or ""), "transfer_code": str(data.get("transfer_code") or ""),
                "message": resp.get("message") or "", "raw": resp}

    def verify_transfer(self, reference):
        resp = _get_json(f"{self.BASE}/transfer/verify/{urllib.parse.quote(reference)}", self._auth)
        data = resp.get("data") or {}
        return {"status": str(data.get("status") or ""), "transfer_code": str(data.get("transfer_code") or ""),
                "reason": str(data.get("reason") or data.get("failures") or ""), "raw": resp}

    def verify(self, reference):
        resp = _get_json(
            f"{self.BASE}/transaction/verify/{urllib.parse.quote(reference)}", self._auth
        )
        data = resp.get("data") or {}
        gw_status = data.get("status")
        status = ("success" if gw_status == "success"
                  else "pending" if gw_status in ("ongoing", "pending", "processing", "queued")
                  else "failed")
        amount = Decimal(data["amount"]) / 100 if data.get("amount") is not None else None
        return {"status": status, "amount": amount,
                "currency": data.get("currency", "NGN"), "raw": resp}

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        # Webhooks are signed with the dashboard's "Webhook signing secret" when the
        # integration uses a restricted API key, otherwise with the secret key itself.
        for secret in (settings.PAYSTACK_WEBHOOK_SECRET, settings.PAYSTACK_SECRET_KEY):
            if secret and hmac.compare_digest(
                    hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest(),
                    signature or ""):
                return True
        return False

    def verify_webhook(self, request):
        return self.verify_signature(
            request.body, request.META.get("HTTP_X_PAYSTACK_SIGNATURE", "")
        )

    def parse_event(self, payload: dict):
        data = payload.get("data", {}) or {}
        reference = data.get("reference")
        status = "success" if payload.get("event") == "charge.success" else "failed"
        amount = Decimal(data["amount"]) / 100 if data.get("amount") is not None else None
        return reference, status, amount


class FlutterwaveGateway(PaymentGateway):
    """
    Flutterwave Standard (v3). Amounts are in Naira, not kobo.

    Flutterwave webhooks are authenticated by the `verif-hash` header matching
    the secret hash configured in the dashboard. The webhook body is NOT trusted
    for value: the caller re-verifies the transaction with the API first.
    """

    name = "flutterwave"
    BASE = "https://api.flutterwave.com/v3"

    def is_configured(self):
        return bool(settings.FLUTTERWAVE_SECRET_KEY)

    @property
    def _auth(self):
        return {"Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}"}

    def initialize(self, *, reference, amount, email, callback_url, name="", description="",
                   split=None):
        if split:
            raise GatewayError("Split payments are only set up on Paystack.")
        resp = _post_json(
            f"{self.BASE}/payments",
            {
                "tx_ref": reference,
                "amount": str(Decimal(amount).quantize(Decimal("0.01"))),
                "currency": "NGN",
                "redirect_url": callback_url,
                "customer": {"email": email, "name": name or email},
                "customizations": {"title": "JobCatch", "description": description[:100]},
            },
            self._auth,
        )
        if resp.get("status") != "success":
            raise GatewayError(f"Flutterwave refused checkout: {resp.get('message')}")
        return {"authorization_url": (resp.get("data") or {}).get("link"),
                "reference": reference}

    def verify(self, reference):
        qs = urllib.parse.urlencode({"tx_ref": reference})
        try:
            resp = _get_json(f"{self.BASE}/transactions/verify_by_reference?{qs}", self._auth)
        except GatewayError as exc:
            # FLW answers 400 "No transaction was found" before the customer pays.
            if "No transaction" in str(exc):
                return {"status": "pending", "amount": None, "currency": "NGN", "raw": {}}
            raise
        data = resp.get("data") or {}
        gw_status = (data.get("status") or "").lower()
        status = ("success" if gw_status == "successful"
                  else "pending" if gw_status in ("pending", "")
                  else "failed")
        amount = Decimal(str(data["amount"])) if data.get("amount") is not None else None
        return {"status": status, "amount": amount,
                "currency": data.get("currency", "NGN"), "raw": resp}

    def verify_webhook(self, request):
        expected = settings.FLUTTERWAVE_WEBHOOK_HASH
        received = request.META.get("HTTP_VERIF_HASH", "")
        return bool(expected) and hmac.compare_digest(expected, received)

    def parse_event(self, payload: dict):
        data = payload.get("data", {}) or {}
        reference = data.get("tx_ref")
        status = "success" if (data.get("status") or "").lower() == "successful" else "failed"
        amount = Decimal(str(data["amount"])) if data.get("amount") is not None else None
        return reference, status, amount


_GATEWAYS = {"paystack": PaystackGateway, "flutterwave": FlutterwaveGateway}


def get_gateway(name: str) -> PaymentGateway:
    cls = _GATEWAYS.get(name)
    if not cls:
        raise ValidationError(f"Unsupported payment gateway: {name}")
    return cls()


def available_gateways():
    """Gateways with credentials configured (the default is always listed first)."""
    names = [settings.DEFAULT_PAYMENT_GATEWAY] + [n for n in _GATEWAYS
                                                   if n != settings.DEFAULT_PAYMENT_GATEWAY]
    return [n for n in names if n in _GATEWAYS and _GATEWAYS[n]().is_configured()]
