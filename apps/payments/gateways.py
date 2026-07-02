"""
Payment gateway abstraction.

A thin interface so additional providers (Flutterwave, etc.) are just new
subclasses — the rest of the app only ever talks to `get_gateway(name)`.
Uses the standard library for HTTP so no extra dependency is required.
"""
import hashlib
import hmac
import json
import urllib.request

from django.conf import settings
from rest_framework.exceptions import ValidationError


def _post_json(url, payload, headers):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _get_json(url, headers):
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


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
