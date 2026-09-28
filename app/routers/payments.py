import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.errors import AppError, unauthorized
from app.models import Payment, User
from app.ratelimit import rate_limit
from app.schemas import Page, PaymentCreate, PaymentOut, WebhookAck, WebhookEventIn
from app.security import verify_webhook_signature
from app.services import payments as service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/", response_model=PaymentOut, status_code=201,
             dependencies=[Depends(rate_limit("payments", "payment_rate_limit_per_minute"))],
             summary="Simulate a payment for a booking",
             responses={200: {"description": "Replay of an earlier request with the same Idempotency-Key"}})
@router.post("", response_model=PaymentOut, status_code=201, include_in_schema=False)
def create_payment(data: PaymentCreate, response: Response,
                   idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128)] = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment, created = service.create_payment(db, user, data, idempotency_key)
    if not created:
        response.status_code = 200
    return payment


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/webhook/", response_model=WebhookAck, summary="Payment-provider webhook (HMAC signed, idempotent)")
@router.post("/webhook", response_model=WebhookAck, include_in_schema=False)
def webhook(body: bytes = Depends(_raw_body), x_signature: Annotated[str | None, Header()] = None,
            db: Session = Depends(get_db)):
    # Authenticate the *provider* by HMAC over the exact raw bytes, before parsing anything.
    if not verify_webhook_signature(body, x_signature):
        raise unauthorized("Invalid webhook signature", "invalid_signature")
    try:
        raw = json.loads(body)
        event = WebhookEventIn.model_validate(raw)
    except (ValueError, ValidationError) as exc:
        raise AppError(422, "invalid_webhook_payload", f"Invalid webhook payload: {str(exc)[:300]}")
    outcome = service.process_webhook(db, event, raw)
    return WebhookAck(status=outcome, event_id=event.event_id)


@router.get("/", response_model=Page[PaymentOut], summary="My payments")
@router.get("", response_model=Page[PaymentOut], include_in_schema=False)
def list_my_payments(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy import func
    stmt = select(Payment).where(Payment.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Payment.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_owned_payment(db, user, payment_id)
