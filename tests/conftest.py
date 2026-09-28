import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256

# Configure BEFORE importing the app: in-memory SQLite, no Redis, no rate limiting by default.
os.environ.update(DATABASE_URL="sqlite://", REDIS_URL="", RATE_LIMIT_ENABLED="false",
                  JWT_SECRET="test-secret-key-that-is-long-enough-for-hs256", BCRYPT_ROUNDS="4", CELERY_EAGER="true", WEBHOOK_SECRET="whsec_test")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, Centre, CentreTest, DiagnosticTest, Role, User
from app.ratelimit import reset_memory
from app.security import hash_password


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_memory()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def signup_login(client, email="alice@example.com", password="Passw0rd123"):
    r = client.post("/auth/signup", json={"email": email, "full_name": "Test User", "password": password})
    assert r.status_code == 201, r.text
    token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_headers(client):
    return signup_login(client)


@pytest.fixture
def other_headers(client):
    return signup_login(client, "bob@example.com")


@pytest.fixture
def admin_headers(client):
    with SessionLocal() as db:
        db.add(User(email="admin@example.com", full_name="Admin", role=Role.ADMIN,
                    hashed_password=hash_password("Admin12345")))
        db.commit()
    token = client.post("/auth/login", json={"email": "admin@example.com", "password": "Admin12345"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def catalog():
    """One test (price 500) offered by one centre; a second centre that does NOT offer it."""
    with SessionLocal() as db:
        t = DiagnosticTest(code="CBC", name="Complete Blood Count")
        c = Centre(name="Centre A", city="Indore", address="Somewhere 1")
        c2 = Centre(name="Centre B", city="Pune", address="Somewhere 2")
        db.add_all([t, c, c2])
        db.flush()
        db.add(CentreTest(centre_id=c.id, test_id=t.id, price=500))
        db.commit()
        return {"test_id": t.id, "centre_id": c.id, "other_centre_id": c2.id}


def future_iso(hours=48):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest.fixture
def booking(client, user_headers, catalog):
    r = client.post("/bookings", headers=user_headers, json={
        "test_id": catalog["test_id"], "centre_id": catalog["centre_id"], "appointment_at": future_iso()})
    assert r.status_code == 201, r.text
    return r.json()


def send_webhook(client, payload, secret="whsec_test", signature=None):
    body = json.dumps(payload).encode()
    sig = signature if signature is not None else hmac.new(secret.encode(), body, sha256).hexdigest()
    return client.post("/payments/webhook/", content=body,
                       headers={"X-Signature": sig, "Content-Type": "application/json"})


def event(event_id, ref, kind="payment.succeeded", **data):
    return {"event_id": event_id, "type": kind, "data": {"payment_reference": ref, **data}}
