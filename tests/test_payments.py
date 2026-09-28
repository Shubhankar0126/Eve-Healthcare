def pay(client, headers, booking_id, outcome=None, extra_headers=None):
    body = {"booking_id": booking_id, **({"simulate_outcome": outcome} if outcome else {})}
    return client.post("/payments/", headers={**headers, **(extra_headers or {})}, json=body)


def booking_status(client, headers, bid):
    return client.get(f"/bookings/{bid}", headers=headers).json()["status"]


def test_successful_payment_confirms_booking(client, user_headers, booking):
    r = pay(client, user_headers, booking["id"], "SUCCESS")
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "SUCCESS" and p["booking_status"] == "CONFIRMED"
    assert p["amount"] == booking["amount"] and p["provider_reference"].startswith("pay_")
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"


def test_failed_payment_marks_booking_failed_then_retry_succeeds(client, user_headers, booking):
    assert pay(client, user_headers, booking["id"], "FAILED").json()["status"] == "FAILED"
    assert booking_status(client, user_headers, booking["id"]) == "FAILED"
    retry = pay(client, user_headers, booking["id"], "SUCCESS")
    assert retry.status_code == 201
    assert booking_status(client, user_headers, booking["id"]) == "CONFIRMED"
    assert client.get("/payments/", headers=user_headers).json()["total"] == 2


def test_random_outcome_is_success_or_failed(client, user_headers, booking):
    assert pay(client, user_headers, booking["id"]).json()["status"] in {"SUCCESS", "FAILED"}


def test_cannot_pay_twice(client, user_headers, booking):
    pay(client, user_headers, booking["id"], "SUCCESS")
    r = pay(client, user_headers, booking["id"], "SUCCESS")
    assert r.status_code == 409 and r.json()["code"] == "already_paid"


def test_cannot_pay_cancelled_booking(client, user_headers, booking):
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    r = pay(client, user_headers, booking["id"], "SUCCESS")
    assert r.status_code == 409 and r.json()["code"] == "booking_cancelled"


def test_auth_and_ownership(client, user_headers, other_headers, booking):
    assert client.post("/payments/", json={"booking_id": booking["id"]}).status_code == 401
    assert pay(client, other_headers, booking["id"], "SUCCESS").status_code == 404
    assert booking_status(client, user_headers, booking["id"]) == "PENDING"


def test_invalid_input(client, user_headers):
    assert pay(client, user_headers, 424242).status_code == 404
    assert client.post("/payments/", headers=user_headers, json={"booking_id": "x"}).status_code == 422
    assert client.post("/payments/", headers=user_headers,
                       json={"booking_id": 1, "simulate_outcome": "MAYBE"}).status_code == 422


def test_idempotency_key_replays_same_payment(client, user_headers, booking):
    h = {"Idempotency-Key": "abc-123"}
    first = pay(client, user_headers, booking["id"], "FAILED", extra_headers=h)
    second = pay(client, user_headers, booking["id"], "SUCCESS", extra_headers=h)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"] and second.json()["status"] == "FAILED"
    assert client.get("/payments/", headers=user_headers).json()["total"] == 1


def test_pending_payment_blocks_a_second_attempt(client, user_headers, booking):
    assert pay(client, user_headers, booking["id"], "PENDING").json()["status"] == "PENDING"
    assert booking_status(client, user_headers, booking["id"]) == "PENDING"
    r = pay(client, user_headers, booking["id"], "SUCCESS")
    assert r.status_code == 409 and r.json()["code"] == "payment_in_progress"


def test_get_payment_ownership(client, user_headers, other_headers, booking):
    pid = pay(client, user_headers, booking["id"], "SUCCESS").json()["id"]
    assert client.get(f"/payments/{pid}", headers=user_headers).status_code == 200
    assert client.get(f"/payments/{pid}", headers=other_headers).status_code == 404
