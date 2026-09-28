"""Redis-backed behaviour. Skipped automatically when no Redis is reachable.
Run with: REDIS_TEST_URL=redis://localhost:6379/15 pytest tests/test_redis.py"""
import os

import pytest
import redis

from app import cache
from app.config import settings
from app.database import SessionLocal
from app.models import Centre

URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")


def _available() -> bool:
    try:
        return redis.Redis.from_url(URL, socket_connect_timeout=0.3).ping()
    except redis.RedisError:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="Redis not available")


@pytest.fixture
def r(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", URL)
    monkeypatch.setattr(cache, "_client", None)
    client = redis.Redis.from_url(URL, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    monkeypatch.setattr(cache, "_client", None)


def test_catalogue_is_served_from_cache(client, catalog, r):
    first = client.get("/centres").json()
    assert any(k.startswith("centres:v0:list") for k in r.keys("*"))
    # change the DB behind the API's back: a cache hit must still return the old data
    with SessionLocal() as db:
        db.add(Centre(name="Sneaky", city="Goa", address="Beach Rd"))
        db.commit()
    assert client.get("/centres").json() == first
    assert client.get("/centres").json()["total"] == 2


def test_admin_write_invalidates_cache(client, catalog, admin_headers, r):
    assert client.get("/centres").json()["total"] == 2
    client.post("/centres", headers=admin_headers, json={"name": "New", "city": "Delhi", "address": "CP Road"})
    assert r.get("ver:centres") == "1"
    assert client.get("/centres").json()["total"] == 3  # fresh, not stale


def test_centre_detail_cached_and_price_update_visible(client, catalog, admin_headers, r):
    cid, tid = catalog["centre_id"], catalog["test_id"]
    assert client.get(f"/centres/{cid}").json()["tests"][0]["price"] == "500.00"
    assert r.keys("centres:v0:detail:*")
    client.put(f"/centres/{cid}/tests/{tid}", headers=admin_headers, json={"price": "650"})
    assert client.get(f"/centres/{cid}").json()["tests"][0]["price"] == "650.00"


def test_rate_limit_counts_in_redis(client, monkeypatch, r):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    codes = [client.post("/auth/login", json={"email": "a@b.com", "password": "x"}).status_code for _ in range(4)]
    assert codes == [401, 401, 429, 429]
    key = r.keys("rl:auth:*")[0]
    assert r.get(key) == "4" and 0 < r.ttl(key) <= 60


def test_app_keeps_working_when_redis_is_down(client, catalog, monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1/0")  # nothing listens here
    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    assert client.get("/centres").status_code == 200
    assert client.get(f"/centres/{catalog['centre_id']}").status_code == 200
    assert client.post("/auth/login", json={"email": "a@b.com", "password": "x"}).status_code == 401  # limiter falls back
    assert client.get("/health").json()["cache"] is False
    monkeypatch.setattr(cache, "_client", None)


def test_health_reports_cache_up(client, r):
    assert client.get("/health").json() == {"status": "ok", "db": True, "cache": True}
