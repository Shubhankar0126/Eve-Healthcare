import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Payment, utcnow
from app.worker import tasks
from app.worker.celery_app import celery_app
from tests.conftest import send_webhook


def make_pending(client, headers, booking):
    return client.post("/payments/", headers=headers,
                       json={"booking_id": booking["id"], "simulate_outcome": "PENDING"}).json()


def backdate(payment_id, minutes):
    with SessionLocal() as db:
        db.get(Payment, payment_id).created_at = utcnow() - timedelta(minutes=minutes)
        db.commit()


# ---------- expiry (beat job) ----------
def test_stale_pending_payment_expires_and_fails_booking(client, user_headers, booking):
    p = make_pending(client, user_headers, booking)
    backdate(p["id"], settings.pending_payment_timeout_minutes + 5)
    assert tasks.expire_stale_payments.apply().get() == 1
    got = client.get(f"/payments/{p['id']}", headers=user_headers).json()
    assert got["status"] == "FAILED" and got["failure_reason"] == "Timed out waiting for provider"
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "FAILED"
    # booking can be paid again
    assert client.post("/payments/", headers=user_headers,
                       json={"booking_id": booking["id"], "simulate_outcome": "SUCCESS"}).status_code == 201
    assert tasks.expire_stale_payments.apply().get() == 0  # idempotent


def test_fresh_and_resolved_payments_are_not_expired(client, user_headers, booking):
    p = make_pending(client, user_headers, booking)  # fresh
    assert tasks.expire_stale_payments.apply().get() == 0
    send_webhook(client, {"event_id": "e1", "type": "payment.succeeded",
                          "data": {"payment_reference": p["provider_reference"]}})
    backdate(p["id"], 999)  # old, but already SUCCESS
    assert tasks.expire_stale_payments.apply().get() == 0
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFIRMED"


# ---------- provider -> webhook delivery with retries ----------
@pytest.fixture
def eager_retries(monkeypatch):
    # In eager mode Celery only re-runs retries inline when exceptions aren't propagated immediately.
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)


def test_delivery_retries_with_backoff_then_succeeds(monkeypatch, eager_retries):
    calls = []

    def flaky(url, body, sig):
        calls.append(1)
        if len(calls) < 3:
            raise tasks.RetryableDelivery("HTTP 503")

    monkeypatch.setattr(tasks, "_post", flaky)
    assert tasks.send_provider_webhook.apply(args=["pay_x", "SUCCESS"]).get() == "evt_pay_x"
    assert len(calls) == 3


def test_delivery_gives_up_after_max_retries(monkeypatch, eager_retries):
    calls = []

    def always_down(*a):
        calls.append(1)
        raise tasks.RetryableDelivery("down")

    monkeypatch.setattr(tasks, "_post", always_down)
    with pytest.raises(tasks.RetryableDelivery):
        tasks.send_provider_webhook.apply(args=["pay_x", "SUCCESS"]).get()
    assert len(calls) == tasks.send_provider_webhook.max_retries + 1


def test_status_classification(monkeypatch):
    class R:
        def __init__(self, code): self.status_code, self.text = code, ""

    for code, retryable in [(200, False), (400, False), (401, False), (404, True), (429, True), (503, True)]:
        monkeypatch.setattr(tasks.httpx, "post", lambda *a, _c=code, **k: R(_c))
        if retryable:
            with pytest.raises(tasks.RetryableDelivery):
                tasks._post("http://x", b"{}", "sig")
        else:
            tasks._post("http://x", b"{}", "sig")


def test_delivery_task_drives_real_webhook_and_double_delivery_is_safe(client, user_headers, booking, monkeypatch):
    p = make_pending(client, user_headers, booking)
    monkeypatch.setattr(tasks, "_post", lambda url, body, sig: send_webhook(client, __import__("json").loads(body), signature=sig).raise_for_status())
    tasks.send_provider_webhook.apply(args=[p["provider_reference"], "SUCCESS"]).get()
    tasks.send_provider_webhook.apply(args=[p["provider_reference"], "SUCCESS"]).get()  # at-least-once redelivery
    assert client.get(f"/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFIRMED"
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1


# ---------- dispatching ----------
def test_pending_payment_schedules_provider_callback(client, user_headers, booking, monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "simulate_provider_callback", True)
    monkeypatch.setattr(tasks.send_provider_webhook, "apply_async", lambda **kw: sent.append(kw))
    p = make_pending(client, user_headers, booking)
    assert len(sent) == 1 and sent[0]["args"][0] == p["provider_reference"]
    assert sent[0]["countdown"] == settings.provider_callback_delay_seconds


def test_no_callback_scheduled_when_disabled(client, user_headers, booking, monkeypatch):
    sent = []
    monkeypatch.setattr(tasks.send_provider_webhook, "apply_async", lambda **kw: sent.append(kw))
    make_pending(client, user_headers, booking)
    assert sent == []


def test_confirmation_notification_sent_once_on_success(client, user_headers, booking, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/payments/", headers=user_headers, json={"booking_id": booking["id"], "simulate_outcome": "SUCCESS"})
    sent = [r for r in caplog.records if r.getMessage() == "notification_sent"]
    assert len(sent) == 1 and sent[0].booking_id == booking["id"]


def test_no_notification_for_failed_payment(client, user_headers, booking, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/payments/", headers=user_headers, json={"booking_id": booking["id"], "simulate_outcome": "FAILED"})
    assert not [r for r in caplog.records if r.getMessage() == "notification_sent"]


def test_notification_skipped_if_booking_cancelled_meanwhile(client, user_headers, booking):
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    assert tasks.send_booking_confirmation.apply(args=[booking["id"]]).get() is False


def test_broker_outage_never_breaks_the_request(client, user_headers, booking, monkeypatch):
    def boom(**kw):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.send_booking_confirmation, "apply_async", boom)
    r = client.post("/payments/", headers=user_headers, json={"booking_id": booking["id"], "simulate_outcome": "SUCCESS"})
    assert r.status_code == 201 and r.json()["booking_status"] == "CONFIRMED"
