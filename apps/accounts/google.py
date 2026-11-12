"""
"Sign in with Google": verify a Google ID token.

The browser (Google Identity Services) or the mobile app gets an ID token from
Google and sends it here. We ask Google's tokeninfo endpoint to validate it —
Google checks the signature and expiry — then check it was issued for one of
OUR client IDs, by Google, for a verified email address.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleTokenError(Exception):
    pass


def verify_id_token(token):
    """Returns {"email", "name", "picture", "sub"} or raises GoogleTokenError."""
    if not token or len(token) > 4096:
        raise GoogleTokenError("Missing Google credential.")
    if not settings.GOOGLE_CLIENT_IDS:
        raise GoogleTokenError("Google sign-in is not configured.")
    url = f"{TOKENINFO_URL}?{urllib.parse.urlencode({'id_token': token})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            info = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise GoogleTokenError("Google could not verify this sign-in.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Google tokeninfo unreachable: %s", exc)
        raise GoogleTokenError("Google sign-in is temporarily unavailable.") from exc

    if info.get("aud") not in settings.GOOGLE_CLIENT_IDS:
        raise GoogleTokenError("This Google sign-in was not meant for JobCatch.")
    if info.get("iss") not in ISSUERS:
        raise GoogleTokenError("Invalid Google sign-in.")
    if int(info.get("exp", 0)) < time.time():
        raise GoogleTokenError("This Google sign-in has expired. Please try again.")
    if str(info.get("email_verified")).lower() != "true" or not info.get("email"):
        raise GoogleTokenError("Your Google account's email isn't verified.")
    return {"email": info["email"].lower(), "name": info.get("name") or "",
            "picture": info.get("picture") or "", "sub": info.get("sub")}
