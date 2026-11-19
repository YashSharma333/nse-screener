"""009_clean_daily_prices_and_securities

Revision ID: 094ee0ad95bf
Revises: a3c5e1b7d902
Create Date: 2026-11-20 00:12:10.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "094ee0ad95bf"
down_revision: str | Sequence[str] | None = "a3c5e1b7d902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop legacy universe tables, recreate daily_prices without index_name, and create securities."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())

    # 1. Drop legacy universe and membership tables
    if "index_membership" in existing_tables:
        op.drop_table("index_membership")
    if "symbol_alias" in existing_tables:
        op.drop_table("symbol_alias")
    if "stock_master" in existing_tables:
        op.drop_table("stock_master")

    # 2. Recreate daily_prices with clean schema (raw-first, no index_name)
    if "daily_prices" in existing_tables:
        op.drop_table("daily_prices")

    op.create_table(
        "daily_prices",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("series", sa.String(length=4), nullable=False, server_default=sa.text("'EQ'")),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("open", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("high", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("low", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("close", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("prev_close", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("market_cap", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("adj_open", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_high", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_low", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_close", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_volume", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("symbol", "date", name="uq_daily_prices_symbol_date"),
    )
    op.create_index("ix_daily_prices_symbol", "daily_prices", ["symbol"], unique=False)
    op.create_index("ix_daily_prices_date", "daily_prices", ["date"], unique=False)

    # 3. Create securities table for master listing information
    if "securities" not in existing_tables:
        op.create_table(
            "securities",
            sa.Column("symbol", sa.String(length=20), primary_key=True, nullable=False),
            sa.Column("isin", sa.String(length=12), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=True),
            sa.Column("series", sa.String(length=4), nullable=True),
            sa.Column("listing_date", sa.Date(), nullable=True),
            sa.Column("first_seen", sa.Date(), nullable=True),
            sa.Column("last_seen", sa.Date(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=10),
                nullable=False,
                server_default=sa.text("'active'"),
            ),
        )

    # 4. Empty adj_factors per delete-all / fresh start rule
    if "adj_factors" in existing_tables:
        op.execute(sa.text("DELETE FROM adj_factors"))


def downgrade() -> None:
    """Recreate old table structures to support alembic downgrade on MySQL."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())

    # 1. Drop securities
    if "securities" in existing_tables:
        op.drop_table("securities")

    # 2. Drop clean daily_prices (preserving existing rows if any)
    existing_price_rows = []
    if "daily_prices" in existing_tables:
        bind = op.get_bind()
        res = bind.execute(
            sa.text(
                "SELECT id, symbol, date, open, high, low, close, prev_close, volume, "
                "adj_open, adj_high, adj_low, adj_close, adj_volume FROM daily_prices"
            )
        )
        existing_price_rows = res.fetchall()
        op.drop_table("daily_prices")

    # 3. Recreate legacy daily_prices (with index_name)
    legacy_prices = op.create_table(
        "daily_prices",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("high", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("low", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("close", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("prev_close", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("index_name", sa.String(length=50), nullable=True),
        sa.Column("adj_open", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_high", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_low", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_close", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("adj_volume", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("symbol", "date", name="uq_symbol_date"),
    )
    op.create_index("ix_daily_prices_symbol", "daily_prices", ["symbol"], unique=False)
    op.create_index("ix_daily_prices_date", "daily_prices", ["date"], unique=False)
    op.create_index("ix_daily_prices_index_name", "daily_prices", ["index_name"], unique=False)

    if existing_price_rows:
        import datetime

        bind = op.get_bind()
        for r in existing_price_rows:
            d_val = datetime.date.fromisoformat(r[2]) if isinstance(r[2], str) else r[2]
            bind.execute(
                legacy_prices.insert().values(
                    id=r[0],
                    symbol=r[1],
                    date=d_val,
                    open=r[3],
                    high=r[4],
                    low=r[5],
                    close=r[6],
                    prev_close=r[7],
                    volume=r[8],
                    adj_open=r[9],
                    adj_high=r[10],
                    adj_low=r[11],
                    adj_close=r[12],
                    adj_volume=r[13],
                )
            )

    # 4. Recreate legacy stock_master
    if "stock_master" not in existing_tables:
        op.create_table(
            "stock_master",
            sa.Column("symbol", sa.String(length=50), primary_key=True, nullable=False),
            sa.Column("company_name", sa.String(length=255), nullable=False),
            sa.Column("sector", sa.String(length=100), nullable=True),
            sa.Column("industry", sa.String(length=100), nullable=True),
            sa.Column("cap_bucket", sa.String(length=50), nullable=True),
            sa.Column("market_cap_cr", sa.Numeric(precision=14, scale=2), nullable=True),
            sa.Column("market_cap_asof", sa.Date(), nullable=True),
            sa.Column("in_universe", sa.Boolean(), server_default=sa.text("0"), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("added_on", sa.Date(), nullable=False),
            sa.Column("flagged_on", sa.Date(), nullable=True),
            sa.Column("benchmark_index", sa.String(length=50), nullable=True),
            sa.Column("source", sa.String(length=100), nullable=True),
            sa.Column("renamed_to", sa.String(length=50), nullable=True),
        )
        op.create_index(
            "ix_stock_master_in_universe", "stock_master", ["in_universe"], unique=False
        )
        op.create_index("ix_stock_master_status", "stock_master", ["status"], unique=False)

    # 5. Recreate legacy symbol_alias
    if "symbol_alias" not in existing_tables:
        op.create_table(
            "symbol_alias",
            sa.Column("old_symbol", sa.String(length=50), primary_key=True, nullable=False),
            sa.Column("new_symbol", sa.String(length=50), primary_key=True, nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=30),
                server_default=sa.text("'candidate'"),
                nullable=False,
            ),
            sa.Column("implied_ratio", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
        )
        op.create_index("ix_symbol_alias_status", "symbol_alias", ["status"], unique=False)

    # 6. Recreate legacy index_membership
    if "index_membership" not in existing_tables:
        op.create_table(
            "index_membership",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("index_code", sa.String(length=50), nullable=False),
            sa.Column("valid_from", sa.Date(), nullable=False),
            sa.Column("valid_to", sa.Date(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "symbol", "index_code", "valid_from", name="uq_membership_symbol_code_valid_from"
            ),
        )
        op.create_index("ix_index_membership_symbol", "index_membership", ["symbol"], unique=False)
        op.create_index(
            "ix_index_membership_index_code", "index_membership", ["index_code"], unique=False
        )
        op.create_index(
            "ix_index_membership_valid_from", "index_membership", ["valid_from"], unique=False
        )
