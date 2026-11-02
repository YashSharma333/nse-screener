"""003_add_adj_factors_and_adjusted_prices

Revision ID: da54eaf58152
Revises: 16c821387900
Create Date: 2026-10-30 13:29:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "da54eaf58152"
down_revision: str | Sequence[str] | None = "16c821387900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "adj_factors" not in existing_tables:
        op.create_table(
            "adj_factors",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("factor", sa.Numeric(precision=18, scale=8), nullable=False),
            sa.Column("ratio_kind", sa.String(length=20), nullable=False),
            sa.Column(
                "detected_at",
                sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("symbol", "date", name="uq_adj_factors_symbol_date"),
        )
        op.create_index("ix_adj_factors_symbol", "adj_factors", ["symbol"], unique=False)
        op.create_index("ix_adj_factors_date", "adj_factors", ["date"], unique=False)

    existing_cols = [c["name"] for c in insp.get_columns("daily_prices")]
    cols_to_add = [
        ("adj_open", sa.Numeric(precision=14, scale=4)),
        ("adj_high", sa.Numeric(precision=14, scale=4)),
        ("adj_low", sa.Numeric(precision=14, scale=4)),
        ("adj_close", sa.Numeric(precision=14, scale=4)),
        ("adj_volume", sa.BigInteger()),
    ]
    for col_name, col_type in cols_to_add:
        if col_name not in existing_cols:
            op.add_column("daily_prices", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_cols = [c["name"] for c in insp.get_columns("daily_prices")]
    for col_name in ["adj_open", "adj_high", "adj_low", "adj_close", "adj_volume"]:
        if col_name in existing_cols:
            op.drop_column("daily_prices", col_name)

    existing_tables = insp.get_table_names()
    if "adj_factors" in existing_tables:
        op.drop_table("adj_factors")
