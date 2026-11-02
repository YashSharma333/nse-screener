"""002_add_prev_close_to_daily_prices

Revision ID: 16c821387900
Revises: b52c36bcb5f3
Create Date: 2026-10-30 13:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "16c821387900"
down_revision: str | Sequence[str] | None = "b52c36bcb5f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_cols = [c["name"] for c in insp.get_columns("daily_prices")]

    if "prev_close" not in existing_cols:
        op.add_column(
            "daily_prices",
            sa.Column("prev_close", sa.Numeric(precision=10, scale=2), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_cols = [c["name"] for c in insp.get_columns("daily_prices")]

    if "prev_close" in existing_cols:
        op.drop_column("daily_prices", "prev_close")
