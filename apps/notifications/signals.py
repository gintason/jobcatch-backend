"""
Event -> notification wiring.

Decoupled via signals so the domain apps don't import the notifications app.
A pre_save hook stashes the previous status on the instance so post_save can
detect a real change.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.bookings.models import BookingStatusHistory
from apps.jobs.models import Application
from apps.payments.models import Payment, PaymentStatus
from apps.verification.models import Verification, VerificationStatus

from .services import notify


# --- capture previous status for change detection ---
def _stash_old_status(sender, instance, **kwargs):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        instance._old_status = old
    else:
        instance._old_status = None


for _model in (Application, Payment, Verification):
    pre_save.connect(_stash_old_status, sender=_model)


# --- bookings: notify the other party on every transition ---
@receiver(post_save, sender=BookingStatusHistory)
def on_booking_transition(sender, instance, created, **kwargs):
    if not created:
        return
    booking = instance.booking
    customer_user = booking.customer.user
    artisan_user = booking.artisan.user
    recipient = artisan_user if instance.changed_by == customer_user else customer_user
    notify(
        recipient,
        kind="booking_alert",
        title=f"Booking {instance.to_status}",
        body=f"Your booking for '{booking.service.title}' is now {instance.to_status}.",
        data={"booking_id": str(booking.id), "status": instance.to_status},
    )


# --- applications: new -> employer; status change -> seeker ---
@receiver(post_save, sender=Application)
def on_application(sender, instance, created, **kwargs):
    if created:
        notify(
            instance.job.employer.user,
            kind="application_alert",
            title="New application received",
            body=f"{instance.seeker.user.full_name} applied to '{instance.job.title}'.",
            data={"application_id": str(instance.id), "job_id": str(instance.job.id)},
        )
        return
    old = getattr(instance, "_old_status", None)
    if old and old != instance.status:
        notify(
            instance.seeker.user,
            kind="application_alert",
            title=f"Application {instance.status}",
            body=f"Your application to '{instance.job.title}' was {instance.status}.",
            data={"application_id": str(instance.id), "status": instance.status},
        )
        # On hire, open a direct chat between employer and the new hire.
        if instance.status == "hired":
            _ensure_hire_conversation(instance)


def _ensure_hire_conversation(application):
    """Create (once) a conversation linking the employer and the hired seeker."""
    from apps.messaging.models import Conversation

    employer_user = application.job.employer.user
    seeker_user = application.seeker.user

    exists = (Conversation.objects
              .filter(job=application.job, participants=employer_user)
              .filter(participants=seeker_user)
              .exists())
    if exists:
        return

    conversation = Conversation.objects.create(job=application.job)
    conversation.participants.add(employer_user, seeker_user)
    notify(
        seeker_user,
        kind="message_alert",
        title="Chat opened with your employer",
        body=f"You were hired for '{application.job.title}'. You can now message them directly.",
        data={"conversation_id": str(conversation.id), "job_id": str(application.job.id)},
    )
    notify(
        employer_user,
        kind="message_alert",
        title="Chat opened with your new hire",
        body=f"You hired {seeker_user.full_name} for '{application.job.title}'.",
        data={"conversation_id": str(conversation.id), "job_id": str(application.job.id)},
    )


# --- payments: notify payer on success ---
@receiver(post_save, sender=Payment)
def on_payment(sender, instance, created, **kwargs):
    old = getattr(instance, "_old_status", None)
    if instance.status == PaymentStatus.SUCCESS and old != PaymentStatus.SUCCESS:
        notify(
            instance.payer,
            kind="payment_alert",
            title="Payment successful",
            body=f"Your payment of NGN {instance.amount} was received.",
            data={"payment_id": str(instance.id), "reference": instance.reference},
        )


# --- verification: notify submitter on approve/reject ---
@receiver(post_save, sender=Verification)
def on_verification(sender, instance, created, **kwargs):
    old = getattr(instance, "_old_status", None)
    if not created and old != instance.status and instance.status in (
        VerificationStatus.APPROVED, VerificationStatus.REJECTED
    ):
        notify(
            instance.user,
            kind="verification_alert",
            title=f"Verification {instance.status}",
            body=f"Your {instance.type} verification was {instance.status}.",
            data={"verification_id": str(instance.id), "status": instance.status},
        )
