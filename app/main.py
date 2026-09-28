import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import cache
from app.config import settings
from app.database import engine
from app.errors import AppError
from app.logging_config import request_id_ctx, setup_logging
from app.models import Base
from app.routers import auth, bookings, catalog, payments

setup_logging(settings.log_level)
log = logging.getLogger("app.request")

DESCRIPTION = """
Diagnostic test booking + simulated payments.

**Try it:** `POST /auth/signup` → click **Authorize** (use email as username) → book → pay.
Admin endpoints (create centres/tests) need the seeded admin user (see README).
"""


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.auto_create_tables:
            Base.metadata.create_all(engine)
        yield

    app = FastAPI(title="EVE Diagnostics Booking API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error", extra={"path": request.url.path})
            response = JSONResponse({"detail": "Internal server error", "code": "internal_error"}, status_code=500)
        response.headers["X-Request-ID"] = rid
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - start) * 1000, 1)})
        request_id_ctx.reset(token)
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code,
                            headers=exc.headers)

    for r in (auth.router, catalog.router, bookings.router, payments.router):
        app.include_router(r)

    @app.get("/health", tags=["meta"])
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        redis_client = cache.get_redis()
        cache_ok = None
        if redis_client:
            try:
                cache_ok = bool(redis_client.ping())
            except Exception:
                cache_ok = False
        return JSONResponse({"status": "ok" if db_ok else "degraded", "db": db_ok, "cache": cache_ok},
                            status_code=200 if db_ok else 503)

    return app


app = create_app()
