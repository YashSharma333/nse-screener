"""010_drop_legacy_tables_and_index_name

Revision ID: 5e2b8c4d7a10
Revises: 094ee0ad95bf
Create Date: 2026-11-21 00:00:00.000000

Removes what the old Bhavcopy ETL left behind. None of it is read by the new data path:

* ``stock_master``, ``symbol_alias``, ``index_membership`` (migration 009 already dropped
  them, but ``init_db()`` used to re-create them empty on every start)
* ``technical_indicators`` (never filled; indicators live in the Parquet cache)
* ``daily_prices.index_name`` (re-added by an old ALTER TABLE hack in ``init_db()``)

Every step checks that the object exists first, so the migration is safe on a clean
database and on one where the old code re-created these objects.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "5e2b8c4d7a10"
down_revision: str | Sequence[str] | None = "094ee0ad95bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TABLES = ("stock_master", "symbol_alias", "index_membership", "technical_indicators")


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())

    for table in LEGACY_TABLES:
        if table in existing_tables:
            op.drop_table(table)

    if "daily_prices" in existing_tables:
        columns = {c["name"] for c in insp.get_columns("daily_prices")}
        indexes = {i["name"] for i in insp.get_indexes("daily_prices")}
        if "ix_daily_prices_index_name" in indexes:
            op.drop_index("ix_daily_prices_index_name", table_name="daily_prices")
        if "index_name" in columns:
            op.drop_column("daily_prices", "index_name")


def downgrade() -> None:
    """Restore the state right after 009: only ``technical_indicators`` came back then.

    The other legacy tables (and ``index_name``) were already gone after 009, so they are
    not re-created here.
    """
    conn = op.get_bind()
    if "technical_indicators" in set(inspect(conn).get_table_names()):
        return

    op.create_table(
        "technical_indicators",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("sma_50", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("sma_200", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("ema_12", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("ema_26", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("rsi_14", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("high_52w", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("low_52w", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("bb_upper", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("bb_middle", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("bb_lower", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("macd_line", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("macd_signal", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("macd_histogram", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("daily_returns", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("volatility_20", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_indicator_symbol_date"),
    )
    op.create_index(
        "ix_technical_indicators_symbol", "technical_indicators", ["symbol"], unique=False
    )
    op.create_index("ix_technical_indicators_date", "technical_indicators", ["date"], unique=False)
