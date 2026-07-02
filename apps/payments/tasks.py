"""Async payment processing (idempotent)."""
from celery import shared_task


@shared_task
def process_successful_payment(reference):
    from .models import Payment, PaymentStatus
    from .services import fulfill_payment

    payment = Payment.objects.filter(reference=reference).first()
    if not payment or payment.status == PaymentStatus.SUCCESS:
        return  # unknown or already processed -> idempotent no-op
    payment.status = PaymentStatus.SUCCESS
    payment.save(update_fields=["status", "updated_at"])
    fulfill_payment(payment)
