from app.config import settings
from tests.conftest import signup_login


def test_signup_and_login_and_me(client):
    h = signup_login(client)
    me = client.get("/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com" and me.json()["role"] == "USER"
    assert "password" not in me.text


def test_duplicate_email_case_insensitive(client):
    signup_login(client)
    r = client.post("/auth/signup", json={"email": "ALICE@example.com", "full_name": "X Y", "password": "Passw0rd123"})
    assert r.status_code == 409 and r.json()["code"] == "email_taken"


def test_signup_validation(client):
    bad = [{"email": "nope", "full_name": "Ab", "password": "Passw0rd123"},
           {"email": "a@b.com", "full_name": "Ab", "password": "short1"},
           {"email": "a@b.com", "full_name": "Ab", "password": "onlyletters"},
           {"email": "a@b.com", "full_name": " ", "password": "Passw0rd123"}]
    for payload in bad:
        assert client.post("/auth/signup", json=payload).status_code == 422


def test_cannot_self_assign_admin_role(client):
    r = client.post("/auth/signup", json={"email": "m@x.com", "full_name": "Mal", "password": "Passw0rd123",
                                          "role": "ADMIN"})
    assert r.status_code == 201 and r.json()["role"] == "USER"


def test_login_failures_are_uniform(client, user_headers):
    wrong_pw = client.post("/auth/login", json={"email": "alice@example.com", "password": "Wrong12345"})
    no_user = client.post("/auth/login", json={"email": "ghost@example.com", "password": "Passw0rd123"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_oauth2_form_login_for_swagger(client, user_headers):
    r = client.post("/auth/token", data={"username": "alice@example.com", "password": "Passw0rd123"})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"


def test_protected_routes_reject_missing_and_bad_tokens(client):
    assert client.get("/auth/me").status_code == 401
    r = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


def test_rate_limit_on_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    codes = [client.post("/auth/login", json={"email": "a@b.com", "password": "x"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
