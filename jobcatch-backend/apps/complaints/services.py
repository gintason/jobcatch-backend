"""Complaint intake + handling notifications."""
import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from apps.common import audit

from .models import ComplaintStatus

logger = logging.getLogger(__name__)

BILL_RECEIPT_RE = re.compile(r"^JCB-\d{8}-([0-9A-F]{8})$")


def _mail(to, subject, body):
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    except Exception:  # noqa: BLE001 — a mail outage must not lose the complaint
        logger.exception("Complaint email to %s failed", to)


def on_submitted(complaint, request=None):
    audit.record(complaint, "complaint.submitted", request=request, category=complaint.category)
    _mail([complaint.email], f"We received your complaint — {complaint.reference}",
          f"Hello {complaint.full_name},\n\n"
          f"Thank you for reporting this to JobCatch. Your complaint reference is "
          f"{complaint.reference}.\n\nSubject: {complaint.subject}\n\n"
          f"Our team reviews every complaint and will respond within 2 business days. "
          f"You can check progress any time at {settings.FRONTEND_URL}/complaints "
          f"using your reference and this email address.\n\n— The JobCatch team")

    # Alert the team: in-app for every admin, plus the support inbox.
    from apps.accounts.models import User, UserRole
    from apps.notifications.models import Notification

    admins = User.objects.filter(role=UserRole.ADMIN, is_staff=True, is_active=True)
    Notification.objects.bulk_create([
        Notification(user=a, kind="admin_review", title=f"New complaint: {complaint.subject[:80]}",
                     body=f"{complaint.get_category_display()} — from {complaint.full_name}.",
                     data={"complaint_id": str(complaint.id), "url": "/admin/reviews?queue=complaints"})
        for a in admins
    ])
    if settings.COMPLAINTS_NOTIFY_EMAIL:
        target = complaint.artisan.user.full_name if complaint.artisan_id else complaint.artisan_name
        _mail([settings.COMPLAINTS_NOTIFY_EMAIL],
              f"[JobCatch complaint] {complaint.reference} · {complaint.get_category_display()}",
              f"Reference: {complaint.reference}\nDate: {timezone.localtime(complaint.created_at):%d %b %Y %H:%M}\n"
              f"From: {complaint.full_name} <{complaint.email}> {complaint.phone}\n"
              f"Category: {complaint.get_category_display()}\n"
              + (f"Artisan: {target}\n" if target else "")
              + (f"Transaction ref: {complaint.transaction_reference}\n" if complaint.transaction_reference else "")
              + f"Subject: {complaint.subject}\n\n{complaint.description}\n\n"
              f"Handle it at {settings.FRONTEND_URL}/admin/reviews")


def update_complaint(complaint, *, admin, status=None, response=None, internal_note=None,
                     request=None):
    """Staff update. A new response (or a resolution) is emailed to the complainant."""
    changed = []
    now = timezone.now()
    if internal_note is not None and internal_note != complaint.internal_note:
        complaint.internal_note = internal_note
        changed.append("internal_note")
    new_response = response is not None and response.strip() and response != complaint.admin_response
    if new_response:
        complaint.admin_response = response.strip()
        complaint.responded_at = now
        changed += ["admin_response", "responded_at"]
    if status and status != complaint.status:
        complaint.status = status
        changed.append("status")
        if status in (ComplaintStatus.RESOLVED, ComplaintStatus.CLOSED):
            complaint.resolved_at = now
            changed.append("resolved_at")
    if not changed:
        return complaint
    complaint.handled_by = admin
    complaint.save(update_fields=changed + ["handled_by", "updated_at"])
    audit.record(complaint, "complaint.updated", actor=admin, request=request,
                 status=complaint.status, responded=bool(new_response))
    if new_response or "status" in changed:
        _mail([complaint.email], f"Update on your complaint — {complaint.reference}",
              f"Hello {complaint.full_name},\n\nStatus: {complaint.get_status_display()}\n\n"
              + (f"{complaint.admin_response}\n\n" if complaint.admin_response else "")
              + f"Reference: {complaint.reference}\n\n— The JobCatch team")
        if complaint.user_id:
            from apps.notifications.services import notify

            notify(complaint.user, kind="complaint_update",
                   title=f"Complaint {complaint.reference}: {complaint.get_status_display()}",
                   body=complaint.admin_response[:300] or "Your complaint was updated.",
                   data={"complaint_id": str(complaint.id), "url": "/complaints"})
    return complaint


def find_transaction(ref):
    """
    Look up a bill order (receipt "JCB-YYYYMMDD-XXXXXXXX" or its transaction ID)
    or a payment (gateway reference or invoice number) so staff see the facts
    next to the complaint. Returns a small dict, or None.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    from apps.billing.models import BillOrder
    from apps.payments.models import Payment

    m = BILL_RECEIPT_RE.match(ref.upper())
    order = (BillOrder.objects.filter(id__startswith=m.group(1).lower()).first() if m
             else BillOrder.objects.filter(request_id=ref).first())
    if order:
        return {"type": "bill_order", "id": str(order.id), "label": order.receipt_number,
                "summary": f"{order.get_service_display()} · {order.provider} · {order.customer_id}",
                "amount": str(order.total), "status": order.get_status_display(),
                "user_email": order.user.email, "created_at": order.created_at,
                "payment_reference": order.payment.reference if order.payment_id else None}
    payment = (Payment.objects.filter(Q(reference=ref) | Q(invoice_number__iexact=ref))
               .select_related("payer").first())
    if payment:
        return {"type": "payment", "id": str(payment.id), "label": payment.reference,
                "summary": f"{payment.get_purpose_display()} · {payment.invoice_number or ''}".strip(" ·"),
                "amount": str(payment.amount), "status": payment.get_status_display(),
                "user_email": payment.payer.email,
                "created_at": payment.created_at, "payment_reference": payment.reference}
    return None
