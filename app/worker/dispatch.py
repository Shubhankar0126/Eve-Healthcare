"""Enqueue helpers. A broker outage must never fail an API request, so every call is best-effort."""
import logging
import random

from app.config import settings

log = logging.getLogger(__name__)


def booking_confirmed(booking_id: int) -> None:
    try:
        from app.worker.tasks import send_booking_confirmation
        send_booking_confirmation.apply_async(args=[booking_id], retry=False)
    except Exception:
        log.exception("task_dispatch_failed", extra={"task": "send_booking_confirmation"})


def payment_pending(payment_reference: str) -> None:
    """Only when the mock provider is asked to call us back on its own (compose enables it)."""
    if not settings.simulate_provider_callback:
        return
    outcome = "SUCCESS" if random.random() < settings.provider_callback_success_rate else "FAILED"
    try:
        from app.worker.tasks import send_provider_webhook
        send_provider_webhook.apply_async(args=[payment_reference, outcome], retry=False,
                                          countdown=settings.provider_callback_delay_seconds)
    except Exception:
        log.exception("task_dispatch_failed", extra={"task": "send_provider_webhook"})
