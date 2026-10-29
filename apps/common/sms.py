"""
Outbound SMS (phone OTPs).

SMS_BACKEND = "termii"  -> Termii (Nigerian SMS gateway, reaches DND numbers on
                           the "dnd" channel once your sender ID is approved)
SMS_BACKEND = "console" -> logs the message; development only.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from .phone import to_international

logger = logging.getLogger(__name__)


class SMSError(Exception):
    pass


def send_sms(phone: str, message: str) -> None:
    backend = settings.SMS_BACKEND
    if backend == "console":
        logger.warning("[SMS console backend] to=%s message=%s", phone, message)
        return
    if backend == "termii":
        return _send_termii(phone, message)
    raise SMSError(f"Unknown SMS_BACKEND '{backend}'.")


def _send_termii(phone, message):
    if not settings.TERMII_API_KEY:
        raise SMSError("SMS is not configured (TERMII_API_KEY missing).")
    payload = {
        "api_key": settings.TERMII_API_KEY,
        "to": to_international(phone),
        "from": settings.TERMII_SENDER_ID,
        "sms": message,
        "type": "plain",
        "channel": settings.TERMII_CHANNEL,
    }
    req = urllib.request.Request(
        f"{settings.TERMII_BASE_URL.rstrip('/')}/api/sms/send",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "JobCatch/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        # Termii explains rejections (sender ID, channel, balance…) in the body.
        detail = exc.read().decode(errors="replace")[:500]
        raise SMSError(f"Termii rejected the SMS (HTTP {exc.code}): {detail} "
                       f"[sender={settings.TERMII_SENDER_ID!r} channel={settings.TERMII_CHANNEL!r}]") from exc
    except (urllib.error.URLError, ValueError) as exc:
        raise SMSError(f"Could not send SMS: {exc}") from exc
    if body.get("code") not in (None, "ok"):
        raise SMSError(f"SMS provider error: {body.get('message', body)}")
