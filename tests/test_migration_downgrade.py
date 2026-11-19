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

    # 1. Upgrade to legacy head 008 before D4 fresh start
    command.upgrade(alembic_cfg, "a3c5e1b7d902")

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

    # 5. Re-upgrade to 008
    command.upgrade(alembic_cfg, "a3c5e1b7d902")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM daily_prices")).scalar()
        assert count == 1


def test_migration_008_creates_and_drops_the_corporate_action_tables(tmp_path):
    """008 adds corporate_actions and symbol_changes; stepping back removes only those."""
    sqlalchemy_url = f"sqlite:///{tmp_path / 'test_008.db'}"
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    # Upgrade to revision 008 (which creates corporate_actions and symbol_changes)
    command.upgrade(alembic_cfg, "a3c5e1b7d902")
    engine = create_engine(sqlalchemy_url)
    insp = inspect(engine)
    assert {"corporate_actions", "symbol_changes"} <= set(insp.get_table_names())
    unique_names = {c["name"] for c in insp.get_unique_constraints("corporate_actions")}
    assert "uq_corporate_actions_key" in unique_names

    command.downgrade(alembic_cfg, "-1")
    tables = set(inspect(engine).get_table_names())
    assert "corporate_actions" not in tables
    assert "symbol_changes" not in tables
    assert "symbol_alias" in tables  # the previous migration is untouched
