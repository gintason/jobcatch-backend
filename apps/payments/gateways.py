"""
Payment gateway abstraction.

A thin interface so additional providers (Flutterwave, etc.) are just new
subclasses — the rest of the app only ever talks to `get_gateway(name)`.
Uses the standard library for HTTP so no extra dependency is required.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request

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
    """Raised when the payment provider rejects or fails a request."""


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
        raise GatewayError(f"Payment provider returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise GatewayError(f"Could not reach payment provider: {exc.reason}") from exc


def _post_json(url, payload, headers):
    return _request_json(url, headers, payload=payload, method="POST")


def _get_json(url, headers):
    return _request_json(url, headers, method="GET")


class PaymentGateway:
    name = None

    def initialize(self, *, reference, amount_kobo, email, callback_url) -> dict:
        raise NotImplementedError

    def verify(self, reference) -> dict:
        raise NotImplementedError

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        raise NotImplementedError

    def parse_event(self, payload: dict):
        raise NotImplementedError


class PaystackGateway(PaymentGateway):
    name = "paystack"
    BASE = "https://api.paystack.co"

    @property
    def _auth(self):
        return {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}

    def initialize(self, *, reference, amount_kobo, email, callback_url):
        resp = _post_json(
            f"{self.BASE}/transaction/initialize",
            {"email": email, "amount": amount_kobo,
             "reference": reference, "callback_url": callback_url},
            self._auth,
        )
        data = resp.get("data", {})
        return {"authorization_url": data.get("authorization_url"),
                "reference": data.get("reference", reference)}

    def verify(self, reference):
        resp = _get_json(f"{self.BASE}/transaction/verify/{reference}", self._auth)
        ok = resp.get("data", {}).get("status") == "success"
        return {"status": "success" if ok else "failed", "raw": resp}

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        computed = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(computed, signature or "")

    def parse_event(self, payload: dict):
        data = payload.get("data", {})
        reference = data.get("reference")
        status = "success" if payload.get("event") == "charge.success" else "failed"
        return reference, status


_GATEWAYS = {"paystack": PaystackGateway}


def get_gateway(name: str) -> PaymentGateway:
    cls = _GATEWAYS.get(name)
    if not cls:
        raise ValidationError(f"Unsupported payment gateway: {name}")
    return cls()
