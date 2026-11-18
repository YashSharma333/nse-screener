"""008_create_corporate_actions_and_symbol_changes

Revision ID: a3c5e1b7d902
Revises: f7c18050340f
Create Date: 2026-11-18 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "a3c5e1b7d902"
down_revision: str | Sequence[str] | None = "f7c18050340f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the corporate_actions and symbol_changes tables (official NSE reports)."""
    existing_tables = inspect(op.get_bind()).get_table_names()

    if "corporate_actions" not in existing_tables:
        op.create_table(
            "corporate_actions",
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("symbol", sa.String(length=50), nullable=False),
            sa.Column("ex_date", sa.Date(), nullable=False),
            sa.Column("series", sa.String(length=10), nullable=False),
            sa.Column("isin", sa.String(length=20), nullable=True),
            sa.Column("company", sa.String(length=255), nullable=True),
            sa.Column("face_value", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("record_date", sa.Date(), nullable=True),
            sa.Column("action_type", sa.String(length=30), nullable=False),
            sa.Column("part_no", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("subject", sa.String(length=500), nullable=False),
            sa.Column("subject_hash", sa.String(length=32), nullable=False),
            sa.Column("old_face_value", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("new_face_value", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("bonus_new_shares", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("bonus_held_shares", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("rights_new", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("rights_held", sa.Numeric(precision=14, scale=4), nullable=True),
            sa.Column("price_factor", sa.Numeric(precision=18, scale=8), nullable=True),
            sa.Column("parse_status", sa.String(length=20), nullable=False),
            sa.Column(
                "fetched_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "symbol",
                "ex_date",
                "series",
                "subject_hash",
                "part_no",
                name="uq_corporate_actions_key",
            ),
        )
        op.create_index("ix_corporate_actions_symbol", "corporate_actions", ["symbol"])
        op.create_index("ix_corporate_actions_ex_date", "corporate_actions", ["ex_date"])
        op.create_index("ix_corporate_actions_action_type", "corporate_actions", ["action_type"])

    if "symbol_changes" not in existing_tables:
        op.create_table(
            "symbol_changes",
            sa.Column("old_symbol", sa.String(length=50), primary_key=True, nullable=False),
            sa.Column("new_symbol", sa.String(length=50), primary_key=True, nullable=False),
            sa.Column("effective_date", sa.Date(), primary_key=True, nullable=False),
            sa.Column("company", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    """Drop the two tables created by this migration (they hold only re-downloadable data)."""
    existing_tables = inspect(op.get_bind()).get_table_names()
    if "symbol_changes" in existing_tables:
        op.drop_table("symbol_changes")
    if "corporate_actions" in existing_tables:
        op.drop_table("corporate_actions")
