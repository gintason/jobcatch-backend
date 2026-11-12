"""
VTU.ng Partner API v1 client (https://api.vtu.ng/v1, sandbox https://sandbox.vtu.ng/v1).

Auth: `Authorization: Bearer vtu_sk_live_...` (sandbox keys start vtu_sk_test_ and
only work against the sandbox host). Plans/packages are cached for 10 minutes.

Purchases are asynchronous: a 201 returns the transaction in `processing`, and
the final state arrives by webhook or `GET /transactions/{reference}`. Every
purchase carries our order's request_id as the `Idempotency-Key`, so re-sending
the same order can never buy twice: VTU.ng replays the original response.

Money safety: a purchase call has three possible outcomes and the caller must
treat them differently —
  VTUResult(transaction)     VTU.ng accepted the order (any status)
  VTUFailed                  a definite refusal (validation, insufficient
                             balance, auth): nothing was created -> safe to refund
  VTUUnknown                 timeout / 5xx / 409 in-progress / rate limit: the
                             order MAY exist -> do NOT refund; look it up later
"""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CATALOG_TTL = 600

# Purchase endpoints by our BillService value.
PURCHASE_PATHS = {
    "airtime": "/bills/airtime",
    "data": "/bills/data",
    "electricity": "/bills/electricity",
    "tv": "/bills/cable-tv",
    "epins": "/bills/epin",
    "betting": "/bills/bet-funding",
}

# HTTP statuses after which we know no transaction was created.
DEFINITE_FAILURE_STATUSES = {400, 401, 402, 403, 404, 422}


class VTUError(Exception):
    def __init__(self, message, code="", http_status=None, body=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.body = body or {}


class VTUFailed(VTUError):
    """Definitive refusal — no transaction was created."""


class VTUUnknown(VTUError):
    """Outcome unknown — look the transaction up before deciding anything."""


class VTUNotConfigured(VTUFailed):
    pass


@dataclass
class VTUResult:
    transaction: dict

    @property
    def status(self):
        return transaction_status(self.transaction)


def transaction_status(tx):
    """`status` is {"value": ..., "is_final": ...} on API resources, a plain string in webhooks."""
    status = (tx or {}).get("status")
    if isinstance(status, dict):
        status = status.get("value")
    return str(status or "")


def is_sandbox():
    return "sandbox." in settings.VTU_BASE_URL


def _error(body):
    err = (body or {}).get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return err.get("code", ""), err.get("message", "")
    return "", ""


class VTUClient:
    def __init__(self):
        self.base = settings.VTU_BASE_URL.rstrip("/")

    # ------------------------------------------------------------ transport
    def _raw(self, method, path, payload=None, params=None, idempotency_key=None, timeout=45):
        """Returns (http_status, parsed_json_body). Raises VTUUnknown on network errors."""
        if not settings.VTU_API_KEY:
            raise VTUNotConfigured("Bill payments are not configured yet.", code="not_configured")
        url = f"{self.base}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        headers = {"Accept": "application/json", "User-Agent": "JobCatch/1.0",
                   "Authorization": f"Bearer {settings.VTU_API_KEY}"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
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

    def _get(self, path, params=None):
        status, body = self._raw("GET", path, params=params)
        if status == 200:
            return body
        code, message = _error(body)
        if status in (401, 403):
            logger.error("VTU.ng refused %s: %s %s", path, status, code)
        raise VTUUnknown(message or f"Biller error ({status}).", code=code,
                         http_status=status, body=body)

    def _verify(self, path, payload):
        status, body = self._raw("POST", path, payload)
        if status == 200:
            return body
        code, message = _error(body)
        if 400 <= status < 500 and status not in (408, 409, 429):
            raise VTUFailed(message or "We couldn't verify that number.", code=code,
                            http_status=status, body=body)
        raise VTUUnknown(message or "Verification is temporarily unavailable.", code=code,
                         http_status=status, body=body)

    # ------------------------------------------------------------ catalog
    def _cached_list(self, key, path, params):
        cached = cache.get(key)
        if cached is not None:
            return cached
        items = self._get(path, params).get("data") or []
        cache.set(key, items, CATALOG_TTL)
        return items

    def data_plans(self, network):
        """[{variation_code, name, amount, fixed_price}]"""
        return self._cached_list(f"vtu1:data-plans:{network}", "/bills/data-plans",
                                 {"network": network})

    def cable_packages(self, provider):
        """[{variation_code, name, amount, fixed_price}]"""
        return self._cached_list(f"vtu1:cable:{provider}", "/bills/cable-packages",
                                 {"provider": provider})

    def wallet(self):
        return self._get("/wallet").get("data") or []

    # ------------------------------------------------------------ verification
    def verify_meter(self, *, disco, meter_number, meter_type):
        body = self._verify("/bills/electricity/verify-meter", {
            "disco_slug": disco, "meter_number": meter_number, "meter_type": meter_type})
        return body.get("verification") or {}

    def verify_smartcard(self, *, provider, smartcard_number):
        body = self._verify("/bills/cable-tv/verify-smartcard", {
            "provider": provider, "smartcard_number": smartcard_number})
        return body.get("verification") or {}

    def verify_bet_account(self, *, provider, account_id):
        body = self._verify("/bills/bet-funding/verify-account", {
            "provider": provider, "account_id": account_id})
        return body.get("validation") or {}

    # ------------------------------------------------------------ purchases
    def purchase(self, service, payload, *, idempotency_key):
        """POST a purchase and classify the outcome (see module docstring)."""
        status, body = self._raw("POST", PURCHASE_PATHS[service], payload,
                                 idempotency_key=idempotency_key)
        tx = body.get("transaction") if isinstance(body, dict) else None
        if status in (200, 201) and isinstance(tx, dict):
            return VTUResult(tx)
        code, message = _error(body)
        if status in DEFINITE_FAILURE_STATUSES:
            raise VTUFailed(message or "The biller declined this purchase.", code=code,
                            http_status=status, body=body)
        raise VTUUnknown(message or f"Unexpected biller response ({status}).", code=code,
                         http_status=status, body=body)

    def get_transaction(self, *, reference=None, idempotency_key=None):
        """
        Look a purchase up by VTU.ng's reference, or by our Idempotency-Key when we
        never got the reference back. VTUFailed(code="not_found") if it doesn't exist.
        """
        params = None
        if not reference:
            reference, params = idempotency_key, {"type": "bill_payment"}
        status, body = self._raw("GET", f"/transactions/{urllib.parse.quote(reference)}",
                                 params=params)
        if status == 200:
            data = body.get("data", body) if isinstance(body, dict) else {}
            tx = data.get("transaction", data) if isinstance(data, dict) else {}
            return VTUResult(tx)
        code, message = _error(body)
        if status == 404:
            raise VTUFailed(message or "Transaction not found.", code="not_found",
                            http_status=status, body=body)
        raise VTUUnknown(message or f"Lookup failed ({status}).", code=code,
                         http_status=status, body=body)

    def epin_image(self, card):
        """WebP bytes for one delivered ePIN pack (inline data URI, else download_url)."""
        image_url = str(card.get("image_url") or "")
        if image_url.startswith("data:") and "," in image_url:
            return base64.b64decode(image_url.split(",", 1)[1])
        download = str(card.get("download_url") or "")
        if not download.startswith(self.base + "/"):
            raise VTUUnknown("No voucher image in the biller's response.")
        req = urllib.request.Request(download, headers={
            "Authorization": f"Bearer {settings.VTU_API_KEY}", "User-Agent": "JobCatch/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VTUUnknown(f"Could not download the voucher image: {exc}") from exc
