"""
VTU.ng API v2 client (https://vtu.ng/wp-json).

Auth: POST /jwt-auth/v1/token with the reseller account's username/password
returns a JWT valid for 7 days; generating a new one invalidates the old one.
We cache it for 6 days and transparently re-login once on a jwt/forbidden
error. Variation lists are public and cached for 10 minutes.

Money safety: a purchase call has three possible outcomes and the caller must
treat them differently —
  VTUResult(ok=True, body)   the API answered with an order (any status)
  VTUFailed                  the API definitively refused (4xx with a known
                             code): nothing was charged -> safe to refund
  VTUUnknown                 timeout / 5xx / garbled reply: the order MAY
                             exist -> do NOT refund; requery by request_id
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "vtu:jwt"
TOKEN_TTL = 6 * 24 * 3600
VARIATION_TTL = 600

# Error codes after which we know no order was created.
DEFINITE_FAILURE_CODES = {
    "missing_fields", "invalid_field", "invalid_service", "invalid_service_id",
    "invalid_variation_id", "invalid_request_id", "below_minimum_amount",
    "above_maximum_amount", "below_customer_arrears", "invalid_product",
    "product_unavailable", "order_failed", "insufficient_funds", "duplicate_order",
    "rate_limit_exceeded", "wallet_busy", "failure", "invalid_value",
}


class VTUError(Exception):
    def __init__(self, message, code="", http_status=None, body=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.body = body or {}


class VTUFailed(VTUError):
    """Definitive refusal — no order was placed."""


class VTUUnknown(VTUError):
    """Outcome unknown — requery before deciding anything."""


class VTUNotConfigured(VTUFailed):
    pass


@dataclass
class VTUResult:
    body: dict

    @property
    def data(self):
        return self.body.get("data") or {}


class VTUClient:
    def __init__(self):
        self.base = settings.VTU_BASE_URL.rstrip("/")

    # ------------------------------------------------------------ transport
    def _raw(self, method, path, payload=None, token=None, timeout=45):
        url = f"{self.base}{path}"
        headers = {"Accept": "application/json", "User-Agent": "JobCatch/1.0"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode() or "{}")
            except ValueError:
                body = {}
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise VTUUnknown(f"Could not reach the biller: {exc}") from exc

    def _login(self):
        if not (settings.VTU_USERNAME and settings.VTU_PASSWORD):
            raise VTUNotConfigured("Bill payments are not configured yet.", code="not_configured")
        status, body = self._raw("POST", "/jwt-auth/v1/token",
                                 {"username": settings.VTU_USERNAME,
                                  "password": settings.VTU_PASSWORD})
        token = body.get("token")
        if status != 200 or not token:
            logger.error("VTU login failed: %s %s", status, body.get("code"))
            raise VTUFailed("Biller authentication failed.", code=body.get("code", "auth"),
                            http_status=status, body=body)
        cache.set(TOKEN_CACHE_KEY, token, TOKEN_TTL)
        return token

    def _token(self):
        return cache.get(TOKEN_CACHE_KEY) or self._login()

    def _call(self, method, path, payload=None, auth=True):
        token = self._token() if auth else None
        status, body = self._raw(method, path, payload, token)
        if auth and status == 403 and str(body.get("code", "")).startswith(("jwt", "rest_forbidden")):
            cache.delete(TOKEN_CACHE_KEY)           # stale/replaced token: log in again once
            status, body = self._raw(method, path, payload, self._login())
        return status, body

    def _order_call(self, path, payload):
        """POST a purchase and classify the outcome (see module docstring)."""
        status, body = self._call("POST", path, payload)
        code = str(body.get("code", ""))
        if status == 200 and code == "success" and body.get("data"):
            return VTUResult(body)
        if code == "duplicate_request_id":
            # We already sent this request_id: the order exists, go requery it.
            raise VTUUnknown("Duplicate request id", code=code, http_status=status, body=body)
        if 400 <= status < 500 and code in DEFINITE_FAILURE_CODES:
            raise VTUFailed(body.get("message") or "The biller declined this purchase.",
                            code=code, http_status=status, body=body)
        raise VTUUnknown(body.get("message") or f"Unexpected biller response ({status}).",
                         code=code, http_status=status, body=body)

    # ------------------------------------------------------------ public data
    def variations(self, kind, service_id=None):
        """kind = "data" | "tv". Cached; returns the list under "data"."""
        key = f"vtu:variations:{kind}:{service_id or 'all'}"
        cached = cache.get(key)
        if cached is not None:
            return cached
        qs = f"?{urllib.parse.urlencode({'service_id': service_id})}" if service_id else ""
        status, body = self._raw("GET", f"/api/v2/variations/{kind}{qs}")
        if status != 200 or body.get("code") != "success":
            raise VTUUnknown(body.get("message") or "Could not load plans.", code=body.get("code", ""))
        items = body.get("data") or []
        cache.set(key, items, VARIATION_TTL)
        return items

    # ------------------------------------------------------------ authenticated
    def balance(self):
        status, body = self._call("GET", "/api/v2/balance")
        if status != 200:
            raise VTUUnknown(body.get("message") or "Balance unavailable", code=body.get("code", ""))
        return body.get("data", {})

    def verify_customer(self, *, customer_id, service_id, variation_id=None):
        payload = {"customer_id": customer_id, "service_id": service_id}
        if variation_id:
            payload["variation_id"] = variation_id
        status, body = self._call("POST", "/api/v2/verify-customer", payload)
        if status == 200 and body.get("code") == "success":
            return body.get("data") or {}
        raise VTUFailed(body.get("message") or "We couldn't verify that number.",
                        code=body.get("code", ""), http_status=status, body=body)

    def buy_airtime(self, *, request_id, phone, service_id, amount):
        return self._order_call("/api/v2/airtime", {
            "request_id": request_id, "phone": phone, "service_id": service_id,
            "amount": int(amount)})

    def buy_data(self, *, request_id, phone, service_id, variation_id):
        return self._order_call("/api/v2/data", {
            "request_id": request_id, "phone": phone, "service_id": service_id,
            "variation_id": str(variation_id)})

    def buy_electricity(self, *, request_id, customer_id, service_id, variation_id, amount):
        return self._order_call("/api/v2/electricity", {
            "request_id": request_id, "customer_id": customer_id, "service_id": service_id,
            "variation_id": variation_id, "amount": int(amount)})

    def buy_tv(self, *, request_id, customer_id, service_id, variation_id,
               subscription_type="change", amount=None):
        payload = {"request_id": request_id, "customer_id": customer_id,
                   "service_id": service_id, "variation_id": str(variation_id)}
        if service_id in ("dstv", "gotv"):
            payload["subscription_type"] = subscription_type or "change"
        if amount is not None:
            payload["amount"] = int(amount)
        return self._order_call("/api/v2/tv", payload)

    def requery(self, request_id):
        status, body = self._call("POST", "/api/v2/requery", {"request_id": request_id})
        if status == 200 and body.get("code") == "success":
            return VTUResult(body)
        if body.get("code") == "order_not_found":
            raise VTUFailed("Order not found at biller.", code="order_not_found",
                            http_status=status, body=body)
        raise VTUUnknown(body.get("message") or "Requery failed.", code=body.get("code", ""),
                         http_status=status, body=body)
