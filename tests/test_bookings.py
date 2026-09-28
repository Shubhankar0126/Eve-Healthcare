from tests.conftest import future_iso


def payload(catalog, **over):
    return {"test_id": catalog["test_id"], "centre_id": catalog["centre_id"],
            "appointment_at": future_iso(), **over}


def test_create_booking_uses_server_side_price(client, user_headers, catalog):
    r = client.post("/bookings", headers=user_headers, json=payload(catalog, amount="1"))  # amount is ignored
    assert r.status_code == 201
    b = r.json()
    assert b["amount"] == "500.00" and b["status"] == "PENDING"
    assert b["test_name"] == "Complete Blood Count" and b["centre_name"] == "Centre A"


def test_requires_auth(client, catalog):
    assert client.post("/bookings", json=payload(catalog)).status_code == 401
    assert client.get("/bookings").status_code == 401


def test_validation_errors(client, user_headers, catalog):
    assert client.post("/bookings", headers=user_headers,
                       json=payload(catalog, appointment_at=future_iso(-5))).status_code == 422
    assert client.post("/bookings", headers=user_headers,
                       json=payload(catalog, appointment_at=future_iso(24 * 400))).status_code == 422
    assert client.post("/bookings", headers=user_headers,
                       json=payload(catalog, appointment_at="not-a-date")).status_code == 422
    assert client.post("/bookings", headers=user_headers, json={"test_id": 0}).status_code == 422


def test_centre_must_offer_test(client, user_headers, catalog):
    r = client.post("/bookings", headers=user_headers, json=payload(catalog, centre_id=catalog["other_centre_id"]))
    assert r.status_code == 404 and r.json()["code"] == "offering_not_found"
    assert client.post("/bookings", headers=user_headers, json=payload(catalog, test_id=999)).status_code == 404


def test_duplicate_slot_blocked_until_cancelled(client, user_headers, catalog):
    p = payload(catalog)
    assert client.post("/bookings", headers=user_headers, json=p).status_code == 201
    dup = client.post("/bookings", headers=user_headers, json=p)
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_booking"
    client.post("/bookings/1/cancel", headers=user_headers)
    assert client.post("/bookings", headers=user_headers, json=p).status_code == 201


def test_ownership_is_enforced(client, user_headers, other_headers, booking):
    bid = booking["id"]
    assert client.get(f"/bookings/{bid}", headers=other_headers).status_code == 404
    assert client.post(f"/bookings/{bid}/cancel", headers=other_headers).status_code == 404
    assert client.get("/bookings", headers=other_headers).json()["total"] == 0
    assert client.get(f"/bookings/{bid}", headers=user_headers).status_code == 200


def test_invalid_booking_id(client, user_headers):
    assert client.get("/bookings/99999", headers=user_headers).status_code == 404
    assert client.get("/bookings/abc", headers=user_headers).status_code == 422


def test_cancel_flow(client, user_headers, booking):
    r = client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    again = client.post(f"/bookings/{booking['id']}/cancel", headers=user_headers)
    assert again.status_code == 409 and again.json()["code"] == "invalid_state"


def test_list_filter_by_status(client, user_headers, booking):
    assert client.get("/bookings", headers=user_headers, params={"status": "PENDING"}).json()["total"] == 1
    assert client.get("/bookings", headers=user_headers, params={"status": "CONFIRMED"}).json()["total"] == 0
    assert client.get("/bookings", headers=user_headers, params={"status": "BOGUS"}).status_code == 422
