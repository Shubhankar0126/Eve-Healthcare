import logging

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.errors import conflict, unauthorized
from app.models import Role, User
from app.ratelimit import rate_limit
from app.schemas import LoginIn, SignupIn, TokenOut, UserOut
from app.security import (DUMMY_HASH, create_access_token, hash_password, verify_password)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"],
                   dependencies=[Depends(rate_limit("auth", "auth_rate_limit_per_minute"))])


def _authenticate(db: Session, email: str, password: str) -> TokenOut:
    user = db.scalar(select(User).where(User.email == email.lower()))
    ok = verify_password(password, user.hashed_password if user else DUMMY_HASH)  # constant-ish time
    if not user or not ok:
        log.info("login_failed")
        raise unauthorized("Incorrect email or password", "invalid_credentials")
    token, expires_in = create_access_token(user.id)
    return TokenOut(access_token=token, expires_in=expires_in)


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(data: SignupIn, db: Session = Depends(get_db)):
    email = data.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise conflict("Email already registered", "email_taken")
    user = User(email=email, full_name=data.full_name, hashed_password=hash_password(data.password),
                role=Role.USER)  # roles are never client-controlled
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("Email already registered", "email_taken")
    return user


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    return _authenticate(db, data.email, data.password)


@router.post("/token", response_model=TokenOut, include_in_schema=True,
             summary="OAuth2 form login (powers the Swagger 'Authorize' button)")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _authenticate(db, form.username, form.password)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
