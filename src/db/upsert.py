"""
Dialect-Agnostic Bulk Upsert Helper
===================================
Provides unified bulk upsert functionality supporting both MySQL
(``INSERT ... ON DUPLICATE KEY UPDATE``) and SQLite
(``INSERT ... ON CONFLICT DO UPDATE``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import UniqueConstraint, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default batch size for upserts to respect SQLite parameter limits
UPSERT_BATCH_SIZE = 500


def _infer_index_elements(model: Any) -> list[str]:
    """Infer unique constraint column names from model table definition."""
    table = getattr(model, "__table__", None)
    if table is None:
        return []

    # Look for the first multi-column unique constraint
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.columns:
            return [col.name for col in constraint.columns]

    # Fall back to primary key columns
    return [col.name for col in table.primary_key.columns]


def upsert_rows(
    session: Session,
    model: Any,
    rows: list[dict[str, Any]],
    update_columns: list[str] | None = None,
    index_elements: list[str] | None = None,
    batch_size: int = UPSERT_BATCH_SIZE,
    coalesce_columns: Sequence[str] | None = ("index_name",),
) -> int:
    """Upsert a list of dictionary records into the database.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.
    model : Any
        SQLAlchemy ORM model class.
    rows : list[dict[str, Any]]
        List of row dicts matching model column attributes.
    update_columns : list[str] | None
        Columns to update when a unique key collision occurs. If None,
        all keys in rows[0] except index_elements and 'id' are updated.
    index_elements : list[str] | None
        Unique constraint columns for conflict detection (required by SQLite).
        Inferred automatically from table unique constraints if None.
    batch_size : int
        Chunk size for batched execution.
    coalesce_columns : Sequence[str] | None
        Columns that should NEVER be overwritten by NULL if the stored value
        in the database is non-NULL (default: ('index_name',)).

    Returns
    -------
    int
        Number of rows processed.
    """
    if not rows:
        return 0

    bind = session.get_bind()
    dialect_name = bind.dialect.name.lower()

    if index_elements is None:
        index_elements = _infer_index_elements(model)

    first_row = rows[0]
    if update_columns is None:
        index_set = set(index_elements) | {"id"}
        update_columns = [col for col in first_row if col not in index_set]

    coalesce_set = set(coalesce_columns or ())
    total_rows = len(rows)

    for start_idx in range(0, total_rows, batch_size):
        chunk = rows[start_idx : start_idx + batch_size]

        if "sqlite" in dialect_name:
            stmt = sqlite_insert(model)
            if update_columns:
                set_dict = {
                    col: (
                        func.coalesce(getattr(stmt.excluded, col), getattr(model, col))
                        if col in coalesce_set and hasattr(model, col)
                        else getattr(stmt.excluded, col)
                    )
                    for col in update_columns
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=index_elements,
                    set_=set_dict,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
            session.execute(stmt, chunk)

        elif "mysql" in dialect_name:
            stmt = mysql_insert(model)
            if update_columns:
                set_dict = {
                    col: (
                        func.coalesce(getattr(stmt.inserted, col), getattr(model, col))
                        if col in coalesce_set and hasattr(model, col)
                        else getattr(stmt.inserted, col)
                    )
                    for col in update_columns
                }
                stmt = stmt.on_duplicate_key_update(set_dict)
            else:
                stmt = stmt.prefix_with("IGNORE")
            session.execute(stmt, chunk)

        else:
            # Generic fallback (e.g. Postgres or others)
            # Default to standard on_conflict_do_update pattern
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(model)
            if update_columns:
                set_dict = {
                    col: (
                        func.coalesce(getattr(stmt.excluded, col), getattr(model, col))
                        if col in coalesce_set and hasattr(model, col)
                        else getattr(stmt.excluded, col)
                    )
                    for col in update_columns
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=index_elements,
                    set_=set_dict,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
            session.execute(stmt, chunk)

    return total_rows
