"""
Unit tests for database schema changes in task D4.
==================================================
Tests run strictly offline with in-memory SQLite and tmp_path:
no network, no live database dependency.
"""

from __future__ import annotations

from datetime import date

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import PROJECT_ROOT
from src.db.models import Base, DailyPrice, Security


@pytest.fixture
def sqlite_session():
    """In-memory SQLite session with clean tables created from Base.metadata."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_models_daily_prices_columns():
    """DailyPrice model has series, market_cap, isin and explicitly no index_name."""
    cols = DailyPrice.__table__.columns

    assert "series" in cols
    assert "market_cap" in cols
    assert "isin" in cols
    assert "index_name" not in cols

    # Verify column definitions
    assert cols["series"].type.length == 4
    assert cols["isin"].type.length == 12
    assert cols["market_cap"].type.precision == 20
    assert cols["market_cap"].type.scale == 2


def test_unique_symbol_date(sqlite_session: Session):
    """Inserting the same (symbol, date) twice fails with IntegrityError."""
    p1 = DailyPrice(
        symbol="INFY",
        date=date(2024, 1, 1),
        series="EQ",
        close=1500.0,
    )
    sqlite_session.add(p1)
    sqlite_session.commit()

    p2 = DailyPrice(
        symbol="INFY",
        date=date(2024, 1, 1),
        series="EQ",
        close=1510.0,
    )
    sqlite_session.add(p2)
    with pytest.raises(IntegrityError):
        sqlite_session.commit()


def test_securities_default_status_active(sqlite_session: Session):
    """Securities table defaults status to 'active' on insert."""
    sec = Security(
        symbol="INFY",
        isin="INE009A01021",
        name="Infosys Limited",
        series="EQ",
    )
    sqlite_session.add(sec)
    sqlite_session.commit()

    saved = sqlite_session.scalar(select(Security).where(Security.symbol == "INFY"))
    assert saved is not None
    assert saved.status == "active"


def test_migration_has_single_head():
    """Alembic migrations must have exactly one linear head revision."""
    alembic_ini = PROJECT_ROOT / "alembic.ini"
    config = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "5e2b8c4d7a10"  # 010: drop legacy tables and index_name


def test_downgrade_exists():
    """Every migration file in migrations/versions must define upgrade and downgrade callables."""
    versions_dir = PROJECT_ROOT / "migrations" / "versions"
    migration_files = list(versions_dir.glob("*.py"))
    assert len(migration_files) >= 10

    for file_path in migration_files:
        if file_path.name.startswith("__"):
            continue
        content = file_path.read_text(encoding="utf-8")
        assert "def upgrade()" in content, f"Missing upgrade() in {file_path.name}"
        assert "def downgrade()" in content, f"Missing downgrade() in {file_path.name}"
