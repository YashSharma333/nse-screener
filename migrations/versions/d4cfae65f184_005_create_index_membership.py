"""005_create_index_membership

Revision ID: d4cfae65f184
Revises: b6162e8d027a
Create Date: 2026-10-30 13:31:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "d4cfae65f184"
down_revision: str | Sequence[str] | None = "b6162e8d027a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "index_membership" not in existing_tables:
        op.create_table(
            "index_membership",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
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
            sa.PrimaryKeyConstraint("id"),
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


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "index_membership" in existing_tables:
        op.drop_table("index_membership")
