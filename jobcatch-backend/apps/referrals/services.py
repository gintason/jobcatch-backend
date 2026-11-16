"""
Referrals: every user has an invite code; an invitee who signs up with it (or
enters it within REFERRAL_APPLY_DAYS of joining) is linked to the inviter.

The inviter is paid REFERRAL_REWARD into their JobCatch wallet — and the
invitee REFERRAL_NEW_USER_BONUS, if set — only after the invitee's first real
purchase: a successful bill of at least REFERRAL_MIN_SPEND, or a completed
delivery. That, a verified email and REFERRAL_MONTHLY_CAP per inviter keep
fake-account farming unprofitable.
"""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Referral, ReferralCode, ReferralStatus

logger = logging.getLogger(__name__)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O/1/I to misread


def code_for(user):
    existing = ReferralCode.objects.filter(user=user).first()
    if existing:
        return existing.code
    for _ in range(10):
        code = "".join(secrets.choice(ALPHABET) for _ in range(7))
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(user=user, code=code).code
        except IntegrityError:
            existing = ReferralCode.objects.filter(user=user).first()
            if existing:                      # created concurrently for this user
                return existing.code
    raise RuntimeError("Could not allocate a referral code.")


def link_for(code):
    return f"{settings.FRONTEND_URL}/register?ref={code}"


def find_referrer(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    rc = ReferralCode.objects.select_related("user").filter(code=code, user__is_active=True).first()
    return rc.user if rc else None


def _has_qualifying_spend(user, since=None):
    """(reference, True) for the user's first qualifying purchase, else (None, False)."""
    from apps.billing.models import BillOrder, BillStatus
    from apps.dispatch.models import Delivery

    bills = BillOrder.objects.filter(user=user, status=BillStatus.SUCCESSFUL,
                                     total__gte=Decimal(settings.REFERRAL_MIN_SPEND))
    trips = Delivery.objects.filter(customer=user, status="delivered")
    if since:
        bills, trips = bills.filter(created_at__gte=since), trips.filter(created_at__gte=since)
    order = bills.order_by("created_at").first()
    if order:
        return order.receipt_number, True
    trip = trips.order_by("created_at").first()
    if trip:
        return trip.reference, True
    return None, False


def attach(referee, code, *, late=False):
    """Link a new user to the inviter whose code they used. Raises ValidationError."""
    if not settings.REFERRAL_ENABLED:
        raise ValidationError({"code": "Referrals are not available right now."})
    referrer = find_referrer(code)
    if referrer is None:
        raise ValidationError({"code": "That referral code isn't valid."})
    if referrer.pk == referee.pk:
        raise ValidationError({"code": "You can't use your own referral code."})
    if Referral.objects.filter(referee=referee).exists():
        raise ValidationError({"code": "You've already used a referral code."})
    if Referral.objects.filter(referrer=referee, referee=referrer).exists():
        raise ValidationError({"code": "You invited this person — codes can't be swapped."})
    if late:
        if referee.created_at < timezone.now() - timedelta(days=settings.REFERRAL_APPLY_DAYS):
            raise ValidationError({"code": f"Codes can only be added within "
                                           f"{settings.REFERRAL_APPLY_DAYS} days of joining."})
        if _has_qualifying_spend(referee)[1]:
            raise ValidationError({"code": "Codes must be added before your first purchase."})
    try:
        ref = Referral.objects.create(referrer=referrer, referee=referee, code=code.strip().upper())
    except IntegrityError as exc:
        raise ValidationError({"code": "You've already used a referral code."}) from exc
    return ref


def check_qualification(user):
    """Called after a bill succeeds or a delivery completes. Pays out at most once."""
    if not settings.REFERRAL_ENABLED:
        return None
    ref = Referral.objects.filter(referee=user, status=ReferralStatus.PENDING).first()
    if ref is None:
        return None
    reference, ok = _has_qualifying_spend(user, since=ref.created_at)
    if not ok or not user.is_email_verified:
        return None
    from apps.billing.models import TxnKind, TxnSource
    from apps.billing.services import _post

    with transaction.atomic():
        ref = Referral.objects.select_for_update().get(pk=ref.pk)
        if ref.status != ReferralStatus.PENDING:
            return ref
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        paid_this_month = Referral.objects.filter(referrer=ref.referrer, status=ReferralStatus.REWARDED,
                                                  rewarded_at__gte=month_start).count()
        ref.qualifying_reference = reference or ""
        if paid_this_month >= settings.REFERRAL_MONTHLY_CAP:
            ref.status = ReferralStatus.REJECTED
            ref.note = "Monthly referral reward limit reached."
            ref.save(update_fields=["status", "note", "qualifying_reference", "updated_at"])
            return ref
        reward = Decimal(settings.REFERRAL_REWARD)
        bonus = Decimal(settings.REFERRAL_NEW_USER_BONUS)
        if reward > 0:
            _post(ref.referrer, kind=TxnKind.CREDIT, source=TxnSource.REFERRAL, amount=reward,
                  reference=f"referral:{ref.pk}:inviter",
                  description=f"Referral reward · {user.full_name.split(' ')[0]} joined JobCatch")
        if bonus > 0:
            _post(user, kind=TxnKind.CREDIT, source=TxnSource.REFERRAL, amount=bonus,
                  reference=f"referral:{ref.pk}:invitee", description="Welcome bonus · referral")
        ref.status, ref.rewarded_at = ReferralStatus.REWARDED, timezone.now()
        ref.reward_amount, ref.referee_bonus = reward, bonus
        ref.save(update_fields=["status", "rewarded_at", "reward_amount", "referee_bonus",
                                "qualifying_reference", "updated_at"])
    _notify(ref)
    return ref


def _notify(ref):
    from apps.notifications.services import notify

    try:
        if ref.reward_amount > 0:
            notify(ref.referrer, kind="referral", title=f"You earned ₦{ref.reward_amount:,.0f}",
                   body=f"{ref.referee.full_name.split(' ')[0]} made their first purchase on JobCatch. "
                        "Your referral reward is in your wallet.", data={"referral": str(ref.pk)})
        if ref.referee_bonus > 0:
            notify(ref.referee, kind="referral", title=f"₦{ref.referee_bonus:,.0f} welcome bonus",
                   body="Thanks for joining through a friend — your bonus is in your wallet.",
                   data={"referral": str(ref.pk)})
    except Exception:  # noqa: BLE001 — never undo a payout over a notification
        logger.exception("Referral notification failed for %s", ref.pk)


def summary(user):
    code = code_for(user)
    refs = list(Referral.objects.filter(referrer=user).select_related("referee").order_by("-created_at")[:100])
    mine = Referral.objects.filter(referee=user).select_related("referrer").first()
    earned = sum((r.reward_amount for r in refs if r.status == ReferralStatus.REWARDED), Decimal("0"))
    can_apply = (settings.REFERRAL_ENABLED and mine is None
                 and user.created_at >= timezone.now() - timedelta(days=settings.REFERRAL_APPLY_DAYS)
                 and not _has_qualifying_spend(user)[1])
    return {
        "enabled": settings.REFERRAL_ENABLED,
        "code": code, "link": link_for(code),
        "reward": str(Decimal(settings.REFERRAL_REWARD)),
        "new_user_bonus": str(Decimal(settings.REFERRAL_NEW_USER_BONUS)),
        "min_spend": str(Decimal(settings.REFERRAL_MIN_SPEND)),
        "stats": {"invited": len(refs),
                  "rewarded": sum(1 for r in refs if r.status == ReferralStatus.REWARDED),
                  "pending": sum(1 for r in refs if r.status == ReferralStatus.PENDING),
                  "earned": str(earned)},
        "referrals": [{"id": str(r.pk), "name": _mask(r.referee.full_name), "status": r.status,
                       "status_label": r.get_status_display(), "reward": str(r.reward_amount),
                       "note": r.note, "joined_at": r.created_at.isoformat(),
                       "rewarded_at": r.rewarded_at.isoformat() if r.rewarded_at else None} for r in refs],
        "referred_by": _mask(mine.referrer.full_name) if mine else None,
        "can_apply": can_apply,
        "apply_days": settings.REFERRAL_APPLY_DAYS,
    }


def _mask(name):
    """'Ada Okafor' → 'Ada O.' (invitees' full names stay private)."""
    parts = (name or "").split()
    if not parts:
        return "JobCatch user"
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")
