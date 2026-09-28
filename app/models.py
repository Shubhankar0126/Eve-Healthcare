import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, Enum, ForeignKey, Index, Numeric,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(e: type[enum.Enum]) -> Enum:
    # Stored as VARCHAR (not a native PG enum) so adding a state never needs an ALTER TYPE.
    return Enum(e, native_enum=False, length=20, values_callable=lambda x: [m.value for m in x])


class Role(str, enum.Enum):
    USER = "USER"
    ADMIN = "ADMIN"


class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"  # accepted by the "provider", final result arrives via webhook
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WebhookStatus(str, enum.Enum):
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"  # valid event, but payment was already in a terminal state


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    hashed_password: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.USER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiagnosticTest(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    description: Mapped[str | None] = mapped_column(Text)


class Centre(Base):
    __tablename__ = "centres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    city: Mapped[str] = mapped_column(String(80), index=True)
    address: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    offerings: Mapped[list["CentreTest"]] = relationship(
        back_populates="centre", cascade="all, delete-orphan", order_by="CentreTest.id", lazy="selectin")


class CentreTest(Base):
    """Which test a centre offers, and at what price (price is per centre)."""
    __tablename__ = "centre_tests"
    __table_args__ = (UniqueConstraint("centre_id", "test_id", name="uq_centre_test"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    centre_id: Mapped[int] = mapped_column(ForeignKey("centres.id", ondelete="CASCADE"), index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    centre: Mapped[Centre] = relationship(back_populates="offerings")
    test: Mapped[DiagnosticTest] = relationship(lazy="selectin")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # DB-level guard against duplicate bookings for the same slot (races included).
        # Cancelled bookings free the slot.
        Index("uq_booking_active_slot", "user_id", "test_id", "centre_id", "appointment_at", unique=True,
              postgresql_where=text("status <> 'CANCELLED'"), sqlite_where=text("status <> 'CANCELLED'")),
        Index("ix_bookings_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"))
    centre_id: Mapped[int] = mapped_column(ForeignKey("centres.id"))
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # price snapshot at booking time
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus), default=BookingStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    test: Mapped[DiagnosticTest] = relationship(lazy="selectin")
    centre: Mapped[Centre] = relationship(lazy="selectin")

    @property
    def test_name(self) -> str:
        return self.test.name

    @property
    def centre_name(self) -> str:
        return self.centre.name


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_payment_user_idem_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus), default=PaymentStatus.PENDING)
    provider_reference: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    booking: Mapped[Booking] = relationship(lazy="selectin")

    @property
    def booking_status(self) -> BookingStatus:
        return self.booking.status


class WebhookEvent(Base):
    """Ledger of processed provider events. The UNIQUE(event_id) is what makes the webhook idempotent."""
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    status: Mapped[WebhookStatus] = mapped_column(_enum(WebhookStatus), default=WebhookStatus.PROCESSED)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
