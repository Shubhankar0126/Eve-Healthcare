def test_public_listing_filter_and_pagination(client, catalog):
    r = client.get("/centres")
    assert r.status_code == 200 and r.json()["total"] == 2
    centre = next(c for c in r.json()["items"] if c["id"] == catalog["centre_id"])
    assert centre["tests"][0]["code"] == "CBC" and centre["tests"][0]["price"] == "500.00"

    assert client.get("/centres", params={"city": "indore"}).json()["total"] == 1
    assert client.get("/centres", params={"test_id": catalog["test_id"]}).json()["total"] == 1
    page = client.get("/centres", params={"page": 2, "page_size": 1}).json()
    assert len(page["items"]) == 1 and page["total"] == 2
    assert client.get("/centres", params={"page_size": 1000}).status_code == 422


def test_get_centre_404(client):
    assert client.get("/centres/999").status_code == 404


def test_admin_only_writes(client, user_headers, admin_headers, catalog):
    body = {"name": "New Centre", "city": "Delhi", "address": "Connaught Place"}
    assert client.post("/centres", json=body).status_code == 401
    assert client.post("/centres", json=body, headers=user_headers).status_code == 403
    r = client.post("/centres", json=body, headers=admin_headers)
    assert r.status_code == 201 and r.json()["tests"] == []


def test_admin_creates_test_and_prices_it(client, admin_headers, catalog):
    t = client.post("/tests", headers=admin_headers, json={"code": "tsh", "name": "Thyroid"})
    assert t.status_code == 201 and t.json()["code"] == "TSH"
    assert client.post("/tests", headers=admin_headers, json={"code": "TSH", "name": "Dup"}).status_code == 409

    url = f"/centres/{catalog['centre_id']}/tests/{t.json()['id']}"
    assert client.put(url, headers=admin_headers, json={"price": "-5"}).status_code == 422
    r = client.put(url, headers=admin_headers, json={"price": "450.50"})
    assert r.status_code == 200 and {x["code"] for x in r.json()["tests"]} == {"CBC", "TSH"}
    assert client.put(f"/centres/999/tests/{t.json()['id']}", headers=admin_headers,
                      json={"price": "1"}).status_code == 404

    assert client.delete(url, headers=admin_headers).status_code == 204
    assert {x["code"] for x in client.get(f"/centres/{catalog['centre_id']}").json()["tests"]} == {"CBC"}


def test_list_tests_search(client, admin_headers):
    client.post("/tests", headers=admin_headers, json={"code": "LIPID", "name": "Lipid Profile"})
    client.post("/tests", headers=admin_headers, json={"code": "CBC", "name": "Complete Blood Count"})
    assert client.get("/tests", params={"q": "lipid"}).json()["total"] == 1
