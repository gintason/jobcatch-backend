"""
OTP email delivery.

Uses Django's EmailMessage (multipart: plain text + branded HTML). Delivery is
attempted asynchronously via Celery, but falls back to sending inline if no
broker/worker is available — an OTP that never arrives is worse than a slightly
slower request.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

SUBJECTS = {
    "email_verify": "Verify your JobCatch email",
    "password_reset": "Reset your JobCatch password",
}

BRAND_NAVY = "#032a75"
BRAND_BLUE = "#0160de"


def _action(purpose):
    return "reset your password" if purpose == "password_reset" else "verify your email address"


def _plain_body(code, purpose):
    return (
        f"Welcome to JobCatch.\n\n"
        f"Use this code to {_action(purpose)}:\n\n"
        f"    {code}\n\n"
        f"The code expires in {settings.OTP_TTL_MINUTES} minutes.\n"
        f"If you didn't request this, you can safely ignore this email.\n\n"
        f"- The JobCatch team\n"
        f"https://jobcatchonline.com"
    )


def _html_body(code, purpose):
    return f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f6fb;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6fb;padding:32px 16px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(3,42,117,.08);">
          <tr>
            <td style="background:{BRAND_NAVY};padding:24px 32px;">
              <span style="color:#ffffff;font-size:20px;font-weight:800;letter-spacing:-.02em;">JobCatch</span>
            </td>
          </tr>
          <tr>
            <td style="padding:32px;">
              <h1 style="margin:0 0 12px;font-size:20px;color:{BRAND_NAVY};">Your verification code</h1>
              <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4a5568;">Use the code below to {_action(purpose)}.</p>
              <div style="text-align:center;margin:0 0 24px;">
                <span style="display:inline-block;padding:16px 28px;background:#eaf1ff;border-radius:12px;font-size:32px;font-weight:800;letter-spacing:.25em;color:{BRAND_BLUE};">{code}</span>
              </div>
              <p style="margin:0 0 8px;font-size:13px;color:#718096;">This code expires in {settings.OTP_TTL_MINUTES} minutes.</p>
              <p style="margin:0;font-size:13px;color:#718096;">If you didn&rsquo;t request this, you can safely ignore this email.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px;background:#f9fafc;">
              <p style="margin:0;font-size:12px;color:#a0aec0;">&copy; JobCatch &middot; jobcatchonline.com</p>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def _build_message(email, code, purpose):
    msg = EmailMultiAlternatives(
        subject=SUBJECTS.get(purpose, "Your JobCatch code"),
        body=_plain_body(code, purpose),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach_alternative(_html_body(code, purpose), "text/html")
    return msg


def send_otp_now(email, code, purpose):
    """Send synchronously. Raises on SMTP failure so callers can log it."""
    _build_message(email, code, purpose).send(fail_silently=False)


@shared_task
def send_otp_email(email, code, purpose):
    send_otp_now(email, code, purpose)


def deliver_otp(email, code, purpose):
    """
    Queue the OTP email; if the broker/worker isn't reachable, send it inline.

    Keeps registration working in environments without a Celery worker instead
    of silently dropping the code.
    """
    try:
        send_otp_email.delay(email, code, purpose)
        return
    except Exception as exc:  # broker unreachable
        logger.warning("Celery unavailable, sending OTP inline: %s", exc)

    try:
        send_otp_now(email, code, purpose)
    except Exception:
        logger.exception("Failed to send OTP email to %s", email)
        raise
