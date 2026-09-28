import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import BookingStatus, PaymentStatus, Role

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------- auth ----------
class SignupIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=64)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("full_name must be at least 2 characters")
        return v

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("password must contain at least one letter and one digit")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=64)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str
    role: Role
    created_at: datetime


# ---------- catalogue ----------
class DiagnosticTestCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=2000)


class DiagnosticTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None


class CentreCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=3, max_length=255)


class PriceIn(BaseModel):
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class CentreTestOut(BaseModel):
    test_id: int
    code: str
    name: str
    price: Decimal


class CentreOut(BaseModel):
    id: int
    name: str
    city: str
    address: str
    tests: list[CentreTestOut]


# ---------- bookings ----------
class BookingCreate(BaseModel):
    test_id: int = Field(gt=0)
    centre_id: int = Field(gt=0)
    appointment_at: datetime

    @field_validator("appointment_at")
    @classmethod
    def _future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:  # naive input is interpreted as UTC
            v = v.replace(tzinfo=timezone.utc)
        v = v.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if v <= now:
            raise ValueError("appointment_at must be in the future")
        if v > now + timedelta(days=180):
            raise ValueError("appointment_at cannot be more than 180 days ahead")
        return v


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    test_id: int
    test_name: str
    centre_id: int
    centre_name: str
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus
    created_at: datetime
    updated_at: datetime


# ---------- payments ----------
class PaymentCreate(BaseModel):
    booking_id: int = Field(gt=0)
    # Mock-provider control: force an outcome (handy for demos/tests). PENDING means
    # "provider accepted it; final status arrives through the webhook".
    simulate_outcome: Literal["SUCCESS", "FAILED", "PENDING"] | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    booking_id: int
    amount: Decimal
    currency: str
    status: PaymentStatus
    provider_reference: str
    failure_reason: str | None
    booking_status: BookingStatus
    created_at: datetime


class WebhookData(BaseModel):
    payment_reference: str = Field(min_length=1, max_length=64)
    failure_reason: str | None = Field(default=None, max_length=255)


class WebhookEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    type: Literal["payment.succeeded", "payment.failed"]
    data: WebhookData


class WebhookAck(BaseModel):
    status: Literal["processed", "duplicate", "ignored"]
    event_id: str
