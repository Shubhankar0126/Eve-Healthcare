from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):  # used by the test-suite only
    _kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}

engine = create_engine(settings.database_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
