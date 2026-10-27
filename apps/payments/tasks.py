"""Async payment processing (idempotent)."""
from celery import shared_task


@shared_task
def process_successful_payment(reference, paid_amount=None, source="webhook"):
    from .services import confirm_payment

    confirm_payment(reference, source=source, paid_amount=paid_amount)
