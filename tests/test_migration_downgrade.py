"""
Unit test verifying safe baseline migration downgrade (F9).
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from config.settings import PROJECT_ROOT


def test_baseline_migration_downgrade_is_safe(tmp_path):
    """Verify that downgrade -1 does not drop price data in production."""
    db_file = tmp_path / "test_migration.db"
    sqlalchemy_url = f"sqlite:///{db_file}"

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    # 1. Upgrade to head
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(sqlalchemy_url)
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "daily_prices" in tables

    # 2. Seed a dummy price record
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO daily_prices (id, symbol, date, close, volume) "
                "VALUES (1, 'TEST', '2024-01-01', 100.0, 1000)"
            )
        )

    # 3. Execute downgrade -1 (and even base)
    command.downgrade(alembic_cfg, "base")

    # 4. Verify daily_prices table still exists and seeded data survived
    insp = inspect(engine)
    tables_after = insp.get_table_names()
    assert "daily_prices" in tables_after

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar()
        assert count == 1

    # 5. Re-upgrade to head
    command.upgrade(alembic_cfg, "head")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar()
        assert count == 1
