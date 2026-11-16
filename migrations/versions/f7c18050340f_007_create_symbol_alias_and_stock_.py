"""007_create_symbol_alias_and_stock_master_renamed_to

Revision ID: f7c18050340f
Revises: 78422e33ba2d
Create Date: 2026-11-16 16:32:05.017230

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "f7c18050340f"
down_revision: str | Sequence[str] | None = "78422e33ba2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema to include symbol_alias table and stock_master.renamed_to."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

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
        op.create_index(
            "ix_symbol_alias_status",
            "symbol_alias",
            ["status"],
            unique=False,
        )

    if "stock_master" in existing_tables:
        columns = [c["name"] for c in insp.get_columns("stock_master")]
        if "renamed_to" not in columns:
            with op.batch_alter_table("stock_master") as batch_op:
                batch_op.add_column(sa.Column("renamed_to", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema to remove symbol_alias table and stock_master.renamed_to."""
    conn = op.get_bind()
    insp = inspect(conn)
    existing_tables = insp.get_table_names()

    if "stock_master" in existing_tables:
        columns = [c["name"] for c in insp.get_columns("stock_master")]
        if "renamed_to" in columns:
            with op.batch_alter_table("stock_master") as batch_op:
                batch_op.drop_column("renamed_to")

    if "symbol_alias" in existing_tables:
        op.drop_table("symbol_alias")
