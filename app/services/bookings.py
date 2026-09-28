import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import conflict, not_found
from app.models import Booking, BookingStatus, CentreTest, PaymentStatus, User
from app.schemas import BookingCreate

log = logging.getLogger(__name__)

# Booking state machine. FAILED can move to CONFIRMED because a failed payment may be retried.
ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED},
    BookingStatus.FAILED: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
    BookingStatus.CONFIRMED: {BookingStatus.CANCELLED},
    BookingStatus.CANCELLED: set(),
}


def create_booking(db: Session, user: User, data: BookingCreate) -> Booking:
    offering = db.scalar(select(CentreTest).where(
        CentreTest.centre_id == data.centre_id, CentreTest.test_id == data.test_id, CentreTest.is_active))
    if offering is None:
        raise not_found("This centre does not offer this test (or it does not exist)", "offering_not_found")

    dup = db.scalar(select(Booking.id).where(
        Booking.user_id == user.id, Booking.test_id == data.test_id, Booking.centre_id == data.centre_id,
        Booking.appointment_at == data.appointment_at, Booking.status != BookingStatus.CANCELLED))
    if dup:
        raise conflict(f"You already have booking {dup} for this slot", "duplicate_booking")

    booking = Booking(user_id=user.id, test_id=data.test_id, centre_id=data.centre_id,
                      appointment_at=data.appointment_at, amount=offering.price,  # price is never client-supplied
                      status=BookingStatus.PENDING)
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:  # lost a race against the partial unique index
        db.rollback()
        raise conflict("You already have a booking for this slot", "duplicate_booking")
    log.info("booking_created", extra={"booking_id": booking.id, "user_id": user.id})
    return booking


def get_owned_booking(db: Session, user: User, booking_id: int, lock: bool = False) -> Booking:
    """404 (not 403) for other people's bookings so IDs can't be probed."""
    stmt = select(Booking).where(Booking.id == booking_id)
    if lock:
        stmt = stmt.with_for_update()
    booking = db.scalar(stmt)
    if booking is None or booking.user_id != user.id:
        raise not_found("Booking not found", "booking_not_found")
    return booking


def list_bookings(db: Session, user: User, status: BookingStatus | None, page: int, page_size: int):
    stmt = select(Booking).where(Booking.user_id == user.id)
    if status:
        stmt = stmt.where(Booking.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Booking.created_at.desc(), Booking.id.desc())
                       .limit(page_size).offset((page - 1) * page_size)).all()
    return items, total


def cancel_booking(db: Session, user: User, booking_id: int) -> Booking:
    booking = get_owned_booking(db, user, booking_id, lock=True)
    if BookingStatus.CANCELLED not in ALLOWED_TRANSITIONS[booking.status]:
        raise conflict(f"Booking is already {booking.status.value}", "invalid_state")
    booking.status = BookingStatus.CANCELLED
    db.commit()
    log.info("booking_cancelled", extra={"booking_id": booking.id})
    return booking


def apply_payment_result(booking: Booking, result: PaymentStatus) -> None:
    """Update booking from a *terminal* payment result. Caller holds the booking row lock."""
    target = BookingStatus.CONFIRMED if result == PaymentStatus.SUCCESS else BookingStatus.FAILED
    if booking.status == target:
        return
    if target not in ALLOWED_TRANSITIONS[booking.status]:
        # e.g. booking was cancelled while the payment was in flight. Never resurrect it;
        # surface for manual refund instead.
        log.warning("payment_result_not_applied", extra={
            "booking_id": booking.id, "booking_status": booking.status.value, "payment_result": result.value})
        return
    booking.status = target
