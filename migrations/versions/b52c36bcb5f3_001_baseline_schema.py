"""001_baseline_schema

Revision ID: b52c36bcb5f3
Revises:
Create Date: 2026-10-30 13:24:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "b52c36bcb5f3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "daily_prices" not in existing_tables:
        op.create_table(
            "daily_prices",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("open", sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column("high", sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column("low", sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column("close", sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column("volume", sa.BigInteger(), nullable=True),
            sa.Column("index_name", sa.String(length=50), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("symbol", "date", name="uq_symbol_date"),
        )
        op.create_index("ix_daily_prices_symbol", "daily_prices", ["symbol"], unique=False)
        op.create_index("ix_daily_prices_date", "daily_prices", ["date"], unique=False)
        op.create_index("ix_daily_prices_index_name", "daily_prices", ["index_name"], unique=False)

    if "technical_indicators" not in existing_tables:
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
        op.create_index(
            "ix_technical_indicators_date", "technical_indicators", ["date"], unique=False
        )


def downgrade() -> None:
    op.drop_table("technical_indicators")
    op.drop_table("daily_prices")
