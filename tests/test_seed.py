from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Centre, CentreTest, DiagnosticTest, User
from app.seed import seed


def test_seed_is_idempotent_and_creates_admin(client):
    seed()
    seed()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(User.id))) == 1
        assert db.scalar(select(func.count(DiagnosticTest.id))) == 6
        assert db.scalar(select(func.count(Centre.id))) == 3
        assert db.scalar(select(func.count(CentreTest.id))) == 18
    r = client.post("/auth/login", json={"email": "admin@example.com", "password": "Admin12345"})
    assert r.status_code == 200
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + r.json()["access_token"]}).json()["role"] == "ADMIN"
