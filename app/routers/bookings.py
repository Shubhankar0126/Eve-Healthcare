from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import BookingStatus, User
from app.schemas import BookingCreate, BookingOut, Page
from app.services import bookings as service

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post("", response_model=BookingOut, status_code=201)
@router.post("/", response_model=BookingOut, status_code=201, include_in_schema=False)
def create_booking(data: BookingCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_booking(db, user, data)


@router.get("", response_model=Page[BookingOut])
@router.get("/", response_model=Page[BookingOut], include_in_schema=False)
def list_my_bookings(status: BookingStatus | None = None, page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items, total = service.list_bookings(db, user, status, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_owned_booking(db, user, booking_id)


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.cancel_booking(db, user, booking_id)
