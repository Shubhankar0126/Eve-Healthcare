import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError, conflict, not_found
from app.models import (Booking, BookingStatus, Payment, PaymentStatus, User, WebhookEvent, WebhookStatus)
from app.schemas import PaymentCreate, WebhookEventIn
from app.models import utcnow
from app.services import bookings as booking_service
from app.worker import dispatch

log = logging.getLogger(__name__)


def _existing_by_key(db: Session, user: User, key: str) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.user_id == user.id, Payment.idempotency_key == key))


def create_payment(db: Session, user: User, data: PaymentCreate, idempotency_key: str | None) -> tuple[Payment, bool]:
    """Returns (payment, created). Replays with the same Idempotency-Key return the original payment."""
    if idempotency_key:
        existing = _existing_by_key(db, user, idempotency_key)
        if existing:
            if existing.booking_id != data.booking_id:
                raise conflict("Idempotency-Key was already used for a different booking", "idempotency_key_reuse")
            return existing, False

    # Lock the booking row: concurrent payment attempts for one booking are serialised.
    booking = booking_service.get_owned_booking(db, user, data.booking_id, lock=True)
    if booking.status == BookingStatus.CONFIRMED:
        raise conflict("Booking is already paid", "already_paid")
    if booking.status == BookingStatus.CANCELLED:
        raise conflict("Cancelled bookings cannot be paid", "booking_cancelled")
    if db.scalar(select(Payment.id).where(Payment.booking_id == booking.id,
                                          Payment.status == PaymentStatus.PENDING).limit(1)):
        raise conflict("A payment for this booking is already in progress", "payment_in_progress")

    outcome = PaymentStatus(data.simulate_outcome) if data.simulate_outcome else (
        PaymentStatus.SUCCESS if random.random() < settings.payment_success_rate else PaymentStatus.FAILED)

    payment = Payment(booking_id=booking.id, user_id=user.id, amount=booking.amount, status=outcome,
                      provider_reference=f"pay_{uuid.uuid4().hex[:24]}", idempotency_key=idempotency_key,
                      failure_reason="Simulated payment declined" if outcome == PaymentStatus.FAILED else None)
    db.add(payment)
    if outcome != PaymentStatus.PENDING:
        booking_service.apply_payment_result(booking, outcome)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key and (existing := _existing_by_key(db, user, idempotency_key)):
            return existing, False  # concurrent replay with the same key
        raise
    log.info("payment_created", extra={"payment_id": payment.id, "booking_id": booking.id,
                                       "status": outcome.value})
    if outcome == PaymentStatus.PENDING:
        dispatch.payment_pending(payment.provider_reference)
    elif booking.status == BookingStatus.CONFIRMED:
        dispatch.booking_confirmed(booking.id)
    return payment, True


def get_owned_payment(db: Session, user: User, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise not_found("Payment not found", "payment_not_found")
    return payment


def process_webhook(db: Session, event: WebhookEventIn, raw_payload: dict) -> str:
    """Idempotent, transactional webhook handling.

    The event ledger insert and the state changes commit together, so:
      * a redelivered event hits UNIQUE(event_id) -> acknowledged as duplicate, nothing is touched;
      * if processing fails midway, everything (including the ledger row) rolls back and the
        provider's retry is processed from scratch.
    """
    record = WebhookEvent(event_id=event.event_id, event_type=event.type, payload=raw_payload)
    db.add(record)
    try:
        db.flush()  # concurrent duplicates block here until the first transaction finishes
    except IntegrityError:
        db.rollback()
        log.info("webhook_duplicate", extra={"event_id": event.event_id})
        return "duplicate"

    payment = db.scalar(select(Payment).where(
        Payment.provider_reference == event.data.payment_reference).with_for_update())
    if payment is None:
        db.rollback()  # don't record the event: the provider may retry once we know the payment
        raise not_found("Unknown payment_reference", "payment_not_found")

    result, booking = "processed", None
    if payment.status != PaymentStatus.PENDING:
        # Terminal state is immutable: late/out-of-order/conflicting events cannot flip it.
        record.status = WebhookStatus.IGNORED
        result = "ignored"
        log.warning("webhook_ignored_terminal_payment", extra={
            "event_id": event.event_id, "payment_id": payment.id, "payment_status": payment.status.value})
    else:
        new_status = PaymentStatus.SUCCESS if event.type == "payment.succeeded" else PaymentStatus.FAILED
        payment.status = new_status
        payment.failure_reason = (event.data.failure_reason or "Payment failed at provider") \
            if new_status == PaymentStatus.FAILED else None
        booking = db.scalar(select(Booking).where(Booking.id == payment.booking_id).with_for_update())
        booking_service.apply_payment_result(booking, new_status)
        log.info("webhook_processed", extra={"event_id": event.event_id, "payment_id": payment.id,
                                             "status": new_status.value})
    db.commit()
    if booking is not None and booking.status == BookingStatus.CONFIRMED:
        dispatch.booking_confirmed(booking.id)
    return result


def expire_stale_payments(db: Session, older_than: timedelta, batch: int = 200) -> int:
    """Fail PENDING payments older than `older_than` (and their bookings). Safe to run concurrently with
    webhooks: each payment is re-checked under a row lock, so whoever commits first wins."""
    cutoff = utcnow() - older_than
    ids = db.scalars(select(Payment.id).where(Payment.status == PaymentStatus.PENDING,
                                              Payment.created_at < cutoff).limit(batch)).all()
    expired = 0
    for pid in ids:
        payment = db.scalar(select(Payment).where(Payment.id == pid).with_for_update(skip_locked=True))
        if payment is None or payment.status != PaymentStatus.PENDING:
            continue  # locked by a webhook, or already resolved
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Timed out waiting for provider"
        booking = db.scalar(select(Booking).where(Booking.id == payment.booking_id).with_for_update())
        booking_service.apply_payment_result(booking, PaymentStatus.FAILED)
        db.commit()
        expired += 1
        log.info("payment_expired", extra={"payment_id": pid, "booking_id": booking.id})
    return expired
