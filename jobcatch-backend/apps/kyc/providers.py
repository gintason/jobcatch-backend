"""
Automated corporate-identity lookups: CAC registration, TIN, director NIN.

KYC_PROVIDER = "dojah"  -> live lookups via Dojah (https://dojah.io)
KYC_PROVIDER = "manual" -> every check is "skipped"; an admin verifies the
                           uploaded documents by hand (works with no API keys)

Each lookup returns a result dict stored on EmployerKYC.checks[key]:
    {"status": passed|failed|error|skipped, "message": str,
     "checked_at": iso8601, "data": {trimmed provider fields}}

Only the fields needed for review are kept — a NIN record can include a photo
and date of birth that we have no business storing.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils import timezone

from .models import CheckStatus

logger = logging.getLogger(__name__)


def _result(status, message, data=None):
    return {"status": status, "message": message,
            "checked_at": timezone.now().isoformat(), "data": data or {}}


# ------------------------------------------------------------------ name matching
_STOPWORDS = {"ltd", "limited", "plc", "nigeria", "nig", "enterprise", "enterprises",
              "and", "the", "company", "co", "services", "global", "int", "intl",
              "international", "ventures", "of", "&"}


def _tokens(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def names_match(entered, official, threshold=0.6):
    """
    Loose company/person name comparison tolerant of "Ltd" vs "Limited",
    word order and middle names. Requires most of the *shorter* name's
    significant words to appear in the other.
    """
    a, b = _tokens(entered), _tokens(official)
    if not a or not b:
        return False
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small & big) / len(small) >= threshold


def digits_only(value):
    return re.sub(r"\D", "", value or "")


# ------------------------------------------------------------------ providers
class ManualProvider:
    name = "manual"

    def _skip(self):
        return _result(CheckStatus.SKIPPED, "Automated lookup is off; an admin will review your documents.")

    def check_cac(self, kyc):
        return self._skip()

    def check_tin(self, kyc):
        return self._skip()

    def check_nin(self, kyc):
        return self._skip()


class DojahProvider:
    name = "dojah"

    def _get(self, path, params):
        url = f"{settings.DOJAH_BASE_URL.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET", headers={
            "AppId": settings.DOJAH_APP_ID,
            "Authorization": settings.DOJAH_SECRET_KEY,   # sent as-is, no "Bearer"
            "Accept": "application/json",
            "User-Agent": "JobCatch/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return 200, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode() or "{}")
            except ValueError:
                body = {}
            return exc.code, body
        except (urllib.error.URLError, ValueError) as exc:
            return 0, {"error": str(exc)}

    def _lookup(self, path, params):
        """Return (entity|None, error_result|None)."""
        if not (settings.DOJAH_APP_ID and settings.DOJAH_SECRET_KEY):
            return None, _result(CheckStatus.ERROR, "Verification service is not configured.")
        code, body = self._get(path, params)
        if code == 200 and isinstance(body.get("entity"), (dict, list)):
            entity = body["entity"]
            if isinstance(entity, list):
                entity = entity[0] if entity else None
            if entity:
                return entity, None
        if code in (400, 404) or (code == 200 and not body.get("entity")):
            # Provider answered: the number doesn't exist / didn't match.
            msg = body.get("error") or body.get("message") or "No record found for this number."
            return None, _result(CheckStatus.FAILED, str(msg)[:200])
        logger.warning("Dojah lookup %s failed: %s %s", path, code, str(body)[:300])
        return None, _result(CheckStatus.ERROR, "The verification service is unavailable. "
                                                "We'll retry, or an admin will review manually.")

    def check_cac(self, kyc):
        rc = digits_only(kyc.cac_number)
        entity, err = self._lookup(settings.DOJAH_CAC_PATH,
                                   {"rc_number": rc, "company_type": kyc.company_type})
        if err:
            return err
        official = entity.get("company_name") or entity.get("name") or ""
        data = {
            "company_name": official,
            "rc_number": entity.get("rc_number") or rc,
            "status": entity.get("company_status") or entity.get("status"),
            "registration_date": entity.get("date_of_registration") or entity.get("registration_date"),
            "address": entity.get("address") or entity.get("branch_address"),
            "type": entity.get("type_of_company") or entity.get("company_type"),
        }
        if not names_match(kyc.registered_name, official):
            return _result(CheckStatus.FAILED,
                           f"CAC record is registered to '{official}', which doesn't match "
                           f"'{kyc.registered_name}'.", data)
        status_text = str(data.get("status") or "").lower()
        if status_text and any(w in status_text for w in ("inactive", "struck", "dissolved")):
            return _result(CheckStatus.FAILED, f"CAC record status is '{data['status']}'.", data)
        return _result(CheckStatus.PASSED, "CAC registration confirmed.", data)

    def check_tin(self, kyc):
        entity, err = self._lookup(settings.DOJAH_TIN_PATH, {"tin": kyc.tin.strip()})
        if err:
            return err
        taxpayer = entity.get("taxpayer_name") or entity.get("name") or ""
        data = {
            "taxpayer_name": taxpayer,
            "cac_reg_number": entity.get("cac_reg_number"),
            "tax_office": entity.get("tax_office"),
        }
        rc_on_tin = digits_only(data.get("cac_reg_number"))
        rc_matches = bool(rc_on_tin) and rc_on_tin == digits_only(kyc.cac_number)
        if not (rc_matches or names_match(kyc.registered_name, taxpayer)):
            return _result(CheckStatus.FAILED,
                           f"TIN belongs to '{taxpayer}', which doesn't match this business.", data)
        return _result(CheckStatus.PASSED, "TIN confirmed.", data)

    def check_nin(self, kyc):
        entity, err = self._lookup(settings.DOJAH_NIN_PATH, {"nin": digits_only(kyc.director_nin)})
        if err:
            return err
        full = " ".join(filter(None, [entity.get("first_name") or entity.get("firstname"),
                                      entity.get("middle_name") or entity.get("middlename"),
                                      entity.get("last_name") or entity.get("surname")]))
        data = {"name_on_record": full}   # deliberately no photo / DOB / address
        if not names_match(kyc.director_name, full):
            return _result(CheckStatus.FAILED,
                           "The NIN doesn't belong to the director named on this application.", data)
        return _result(CheckStatus.PASSED, "Director identity confirmed.", data)


_PROVIDERS = {"manual": ManualProvider, "dojah": DojahProvider}


def get_provider():
    return _PROVIDERS.get(settings.KYC_PROVIDER, ManualProvider)()
