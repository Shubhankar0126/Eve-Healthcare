"""Idempotent seed: an admin user and a demo catalogue (run `alembic upgrade head` first).  Run: python -m app.seed"""
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Centre, CentreTest, DiagnosticTest, Role, User
from app.security import hash_password

TESTS = [("CBC", "Complete Blood Count"), ("LIPID", "Lipid Profile"), ("HBA1C", "HbA1c (Glycated Haemoglobin)"),
         ("TSH", "Thyroid Stimulating Hormone"), ("VITD", "Vitamin D (25-OH)"), ("LFT", "Liver Function Test")]
CENTRES = [
    ("EVE Diagnostics Koramangala", "Bengaluru", "80 Feet Rd, Koramangala 4th Block"),
    ("EVE Diagnostics Andheri", "Mumbai", "Veera Desai Rd, Andheri West"),
    ("EVE Diagnostics Vijay Nagar", "Indore", "Scheme 54, Vijay Nagar"),
]
PRICES = {"CBC": 350, "LIPID": 700, "HBA1C": 550, "TSH": 450, "VITD": 1200, "LFT": 800}


def seed() -> None:
    with SessionLocal() as db:
        if not db.scalar(select(User.id).where(User.email == settings.admin_email.lower())):
            db.add(User(email=settings.admin_email.lower(), full_name="Admin",
                        hashed_password=hash_password(settings.admin_password), role=Role.ADMIN))
        tests = {}
        for code, name in TESTS:
            t = db.scalar(select(DiagnosticTest).where(DiagnosticTest.code == code)) or DiagnosticTest(code=code, name=name)
            db.add(t)
            tests[code] = t
        db.flush()
        for i, (name, city, address) in enumerate(CENTRES):
            c = db.scalar(select(Centre).where(Centre.name == name))
            if c is None:
                c = Centre(name=name, city=city, address=address)
                db.add(c)
                db.flush()
                for code, base in PRICES.items():
                    db.add(CentreTest(centre_id=c.id, test_id=tests[code].id, price=Decimal(base + i * 50)))
        db.commit()
    print(f"Seeded. Admin: {settings.admin_email} / {settings.admin_password}")


if __name__ == "__main__":
    seed()
