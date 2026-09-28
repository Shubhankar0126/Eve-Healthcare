"""Migrations must build the same schema as the models. Needs PostgreSQL:
   MIGRATION_TEST_URL=postgresql+psycopg://eve:eve@localhost:5432/eve_migrations pytest tests/test_migrations.py"""
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.models import Base

URL = os.environ.get("MIGRATION_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MIGRATION_TEST_URL (PostgreSQL) not set")


def _cfg():
    cfg = Config("alembic.ini")
    cfg.attributes.update(url=URL, configure_logger=False)
    return cfg


@pytest.fixture
def pg():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    yield engine
    engine.dispose()


def test_upgrade_matches_models_and_downgrade_is_clean(pg):
    command.upgrade(_cfg(), "head")
    with pg.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
        idx = {i["name"] for i in inspect(conn).get_indexes("bookings")}
        assert "uq_booking_active_slot" in idx
    command.downgrade(_cfg(), "base")
    with pg.connect() as conn:
        assert set(inspect(conn).get_table_names()) == {"alembic_version"}
