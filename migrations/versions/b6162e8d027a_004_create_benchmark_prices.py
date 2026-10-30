"""004_create_benchmark_prices

Revision ID: b6162e8d027a
Revises: da54eaf58152
Create Date: 2026-10-30 13:31:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "b6162e8d027a"
down_revision: str | Sequence[str] | None = "da54eaf58152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "benchmark_prices" not in existing_tables:
        op.create_table(
            "benchmark_prices",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("index_code", sa.String(length=40), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("close", sa.Numeric(precision=14, scale=4), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("index_code", "date", name="uq_benchmark_code_date"),
        )
        op.create_index(
            "ix_benchmark_prices_index_code", "benchmark_prices", ["index_code"], unique=False
        )
        op.create_index("ix_benchmark_prices_date", "benchmark_prices", ["date"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "benchmark_prices" in existing_tables:
        op.drop_table("benchmark_prices")
