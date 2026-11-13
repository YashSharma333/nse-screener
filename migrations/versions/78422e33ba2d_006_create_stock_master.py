"""006_create_stock_master

Revision ID: 78422e33ba2d
Revises: d4cfae65f184
Create Date: 2026-11-13 14:50:30.332596

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "78422e33ba2d"
down_revision: str | Sequence[str] | None = "d4cfae65f184"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema to include stock_master table."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

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
        )
        op.create_index(
            "ix_stock_master_in_universe", "stock_master", ["in_universe"], unique=False
        )
        op.create_index("ix_stock_master_status", "stock_master", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema to remove stock_master table."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "stock_master" in existing_tables:
        op.drop_table("stock_master")
