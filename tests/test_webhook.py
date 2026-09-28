from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Payment, WebhookEvent
from tests.conftest import event, send_webhook


def pending_payment(client, headers, booking):
    r = client.post("/payments/", headers=headers,
                    json={"booking_id": booking["id"], "simulate_outcome": "PENDING"})
    assert r.status_code == 201
    return r.json()


def counts():
    with SessionLocal() as db:
        return db.scalar(select(func.count(Payment.id))), db.scalar(select(func.count(WebhookEvent.id)))


def status(client, headers, bid):
    return client.get(f"/bookings/{bid}", headers=headers).json()["status"]


def test_signature_required_and_verified(client, user_headers, booking):
    ref = pending_payment(client, user_headers, booking)["provider_reference"]
    ev = event("evt_1", ref)
    assert send_webhook(client, ev, signature="").status_code == 401
    assert send_webhook(client, ev, signature="deadbeef").status_code == 401
    assert send_webhook(client, ev, secret="wrong-secret").status_code == 401
    assert client.post("/payments/webhook/", json=ev).status_code == 401  # header missing
    assert status(client, user_headers, booking["id"]) == "PENDING"


def test_invalid_payloads(client):
    assert send_webhook(client, {"foo": "bar"}).status_code == 422
    assert send_webhook(client, event("e", "ref", kind="payment.refunded")).status_code == 422
    assert send_webhook(client, event("", "ref")).status_code == 422


def test_unknown_payment_is_404_and_not_recorded(client):
    assert send_webhook(client, event("evt_x", "pay_missing")).status_code == 404
    assert counts()[1] == 0  # so the provider's retry will be processed


def test_success_webhook_confirms_booking(client, user_headers, booking):
    p = pending_payment(client, user_headers, booking)
    r = send_webhook(client, event("evt_1", p["provider_reference"]))
    assert r.status_code == 200 and r.json() == {"status": "processed", "event_id": "evt_1"}
    assert status(client, user_headers, booking["id"]) == "CONFIRMED"
    assert client.get(f"/payments/{p['id']}", headers=user_headers).json()["status"] == "SUCCESS"


def test_failed_webhook_fails_booking(client, user_headers, booking):
    p = pending_payment(client, user_headers, booking)
    send_webhook(client, event("evt_f", p["provider_reference"], "payment.failed", failure_reason="Card declined"))
    got = client.get(f"/payments/{p['id']}", headers=user_headers).json()
    assert got["status"] == "FAILED" and got["failure_reason"] == "Card declined"
    assert status(client, user_headers, booking["id"]) == "FAILED"


def test_duplicate_event_is_idempotent(client, user_headers, booking):
    p = pending_payment(client, user_headers, booking)
    ev = event("evt_dup", p["provider_reference"])
    results = [send_webhook(client, ev) for _ in range(5)]
    assert all(r.status_code == 200 for r in results)
    assert [r.json()["status"] for r in results] == ["processed"] + ["duplicate"] * 4
    assert counts() == (1, 1)  # no extra payments, one ledger row
    assert status(client, user_headers, booking["id"]) == "CONFIRMED"
    assert client.get("/bookings", headers=user_headers).json()["total"] == 1


def test_conflicting_later_event_cannot_flip_terminal_state(client, user_headers, booking):
    p = pending_payment(client, user_headers, booking)
    send_webhook(client, event("evt_a", p["provider_reference"]))
    r = send_webhook(client, event("evt_b", p["provider_reference"], "payment.failed"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert client.get(f"/payments/{p['id']}", headers=user_headers).json()["status"] == "SUCCESS"
    assert status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_webhook_for_synchronous_terminal_payment_is_ignored(client, user_headers, booking):
    p = client.post("/payments/", headers=user_headers,
                    json={"booking_id": booking["id"], "simulate_outcome": "FAILED"}).json()
    r = send_webhook(client, event("evt_late", p["provider_reference"]))
    assert r.json()["status"] == "ignored"
    assert status(client, user_headers, booking["id"]) == "FAILED"


def test_webhook_never_resurrects_cancelled_booking(client, user_headers, booking):
    p = pending_payment(client, user_headers, booking)
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    send_webhook(client, event("evt_c", p["provider_reference"]))
    assert status(client, user_headers, booking["id"]) == "CANCELLED"
    assert client.get(f"/payments/{p['id']}", headers=user_headers).json()["status"] == "SUCCESS"  # recorded for refund
