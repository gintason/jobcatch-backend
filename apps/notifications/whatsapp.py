"""
WhatsApp messages via the Meta WhatsApp Cloud API.

Business-initiated messages (a job alert the user didn't just ask for) must use
a pre-approved *template*. Create one in WhatsApp Manager named
WHATSAPP_JOB_ALERT_TEMPLATE with three body variables:
    "New job on JobCatch: {{1}} at {{2}}. View and apply: {{3}}"

WHATSAPP_BACKEND = "cloud_api" sends for real; "console" just logs.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from apps.common.phone import to_international

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v20.0"


class WhatsAppError(Exception):
    pass


def send_template(phone, template, params, lang=None):
    to = to_international(phone)
    if settings.WHATSAPP_BACKEND == "console":
        logger.warning("[WhatsApp console] to=%s template=%s params=%s", to, template, params)
        return {"console": True}
    if settings.WHATSAPP_BACKEND != "cloud_api":
        raise WhatsAppError(f"Unknown WHATSAPP_BACKEND '{settings.WHATSAPP_BACKEND}'.")
    if not (settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID):
        raise WhatsAppError("WhatsApp is not configured.")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang or settings.WHATSAPP_TEMPLATE_LANG},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)[:300]} for p in params],
            }],
        },
    }
    req = urllib.request.Request(
        f"{GRAPH_URL}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise WhatsAppError(f"WhatsApp API {exc.code}: "
                            f"{exc.read().decode(errors='replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise WhatsAppError(f"Could not reach WhatsApp: {exc.reason}") from exc
