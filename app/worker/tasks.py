"""Background jobs. Every task is idempotent, so at-least-once delivery (acks_late) is safe."""
import json
import logging
from datetime import timedelta

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import Booking, BookingStatus
from app.security import sign_webhook
from app.services import payments as payment_service
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)


class RetryableDelivery(Exception):
    """Webhook delivery failed in a way that is worth retrying (network error, 5xx, 429, 404)."""


def _post(url: str, body: bytes, signature: str) -> None:
    try:
        resp = httpx.post(url, content=body, timeout=5,
                          headers={"Content-Type": "application/json", "X-Signature": signature})
    except httpx.HTTPError as exc:
        raise RetryableDelivery(f"network error: {exc}") from exc
    # 404 is retryable: the payment row may not be visible yet. Other 4xx will never succeed.
    if resp.status_code in (404, 408, 429) or resp.status_code >= 500:
        raise RetryableDelivery(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        log.error("webhook_delivery_rejected", extra={"status": resp.status_code, "body": resp.text[:200]})


@celery_app.task(name="app.worker.tasks.send_provider_webhook", autoretry_for=(RetryableDelivery,),
                 retry_backoff=2, retry_backoff_max=300, retry_jitter=True, max_retries=6)
def send_provider_webhook(payment_reference: str, outcome: str) -> str:
    """Mock payment provider: report the final payment status to our webhook, with exponential-backoff retries.
    The event_id is deterministic, so redeliveries/retries are collapsed by the webhook's idempotency ledger."""
    event_id = f"evt_{payment_reference}"
    body = json.dumps({
        "event_id": event_id,
        "type": "payment.succeeded" if outcome == "SUCCESS" else "payment.failed",
        "data": {"payment_reference": payment_reference,
                 **({"failure_reason": "Declined by provider"} if outcome != "SUCCESS" else {})},
    }).encode()
    _post(settings.webhook_target_url, body, sign_webhook(body))
    log.info("webhook_delivered", extra={"event_id": event_id, "outcome": outcome})
    return event_id


@celery_app.task(name="app.worker.tasks.expire_stale_payments")
def expire_stale_payments() -> int:
    """Beat job: PENDING payments the provider never resolved are failed, so bookings don't hang forever."""
    with SessionLocal() as db:
        count = payment_service.expire_stale_payments(
            db, timedelta(minutes=settings.pending_payment_timeout_minutes))
    if count:
        log.info("stale_payments_expired", extra={"count": count})
    return count


@celery_app.task(name="app.worker.tasks.send_booking_confirmation", autoretry_for=(Exception,),
                 retry_backoff=2, retry_backoff_max=120, max_retries=3)
def send_booking_confirmation(booking_id: int) -> bool:
    """Simulated email/SMS. Swap the log line for a real provider call."""
    with SessionLocal() as db:
        booking = db.get(Booking, booking_id)
        if booking is None or booking.status != BookingStatus.CONFIRMED:
            return False  # cancelled/changed since enqueue: nothing to notify
        log.info("notification_sent", extra={"channel": "email", "booking_id": booking.id,
                                             "user_id": booking.user_id, "centre": booking.centre.name,
                                             "appointment_at": booking.appointment_at.isoformat()})
    return True
