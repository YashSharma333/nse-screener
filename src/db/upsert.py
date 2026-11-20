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


def _build_upsert_statement(
    model: Any,
    update_columns: Sequence[str] | None,
    index_elements: Sequence[str],
    coalesce_columns: Sequence[str] | None,
    dialect_name: str,
) -> Any:
    """Build a dialect-specific upsert statement for SQLite, MySQL, or Postgres.

    Parameters
    ----------
    model : Any
        SQLAlchemy ORM model or Table.
    update_columns : Sequence[str] | None
        Columns to update on collision. If empty, conflict is ignored.
    index_elements : Sequence[str]
        Unique constraint columns for conflict detection.
    coalesce_columns : Sequence[str] | None
        Columns that should retain existing DB values if incoming value is NULL.
    dialect_name : str
        Name of dialect (e.g. 'sqlite', 'mysql').

    Returns
    -------
    Any
        Constructed SQLAlchemy dialect-specific Insert statement.
    """
    coalesce_set = set(coalesce_columns or ())
    update_cols = list(update_columns or [])

    if "sqlite" in dialect_name:
        stmt = sqlite_insert(model)
        if update_cols:
            set_dict = {
                col: (
                    func.coalesce(getattr(stmt.excluded, col), getattr(model, col))
                    if col in coalesce_set and hasattr(model, col)
                    else getattr(stmt.excluded, col)
                )
                for col in update_cols
                if hasattr(stmt.excluded, col)
            }
            return stmt.on_conflict_do_update(
                index_elements=list(index_elements),
                set_=set_dict,
            )
        return stmt.on_conflict_do_nothing(index_elements=list(index_elements))

    elif "mysql" in dialect_name:
        stmt = mysql_insert(model)
        if update_cols:
            set_dict = {
                col: (
                    func.coalesce(getattr(stmt.inserted, col), getattr(model, col))
                    if col in coalesce_set and hasattr(model, col)
                    else getattr(stmt.inserted, col)
                )
                for col in update_cols
                if hasattr(stmt.inserted, col)
            }
            return stmt.on_duplicate_key_update(set_dict)
        return stmt.prefix_with("IGNORE")

    else:
        # Generic fallback (e.g. Postgres or others)
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(model)
        if update_cols:
            set_dict = {
                col: (
                    func.coalesce(getattr(stmt.excluded, col), getattr(model, col))
                    if col in coalesce_set and hasattr(model, col)
                    else getattr(stmt.excluded, col)
                )
                for col in update_cols
                if hasattr(stmt.excluded, col)
            }
            return stmt.on_conflict_do_update(
                index_elements=list(index_elements),
                set_=set_dict,
            )
        return stmt.on_conflict_do_nothing(index_elements=list(index_elements))


def _execute_upsert_chunk(
    session: Session,
    model: Any,
    chunk: list[dict[str, Any]],
    update_columns: Sequence[str] | None,
    index_elements: Sequence[str],
    coalesce_columns: Sequence[str] | None,
    dialect_name: str,
) -> None:
    """Build and execute a dialect upsert statement for a single chunk of rows.

    Always passes execution_options={"render_nulls": True} so that None values
    are preserved in the INSERT projection list instead of being omitted by SQLAlchemy.
    """
    stmt = _build_upsert_statement(
        model=model,
        update_columns=update_columns,
        index_elements=index_elements,
        coalesce_columns=coalesce_columns,
        dialect_name=dialect_name,
    )
    session.execute(stmt, chunk, execution_options={"render_nulls": True})


def upsert_rows(
    session: Session,
    model: Any,
    rows: list[dict[str, Any]],
    update_columns: Sequence[str] | None = None,
    index_elements: Sequence[str] | None = None,
    batch_size: int = UPSERT_BATCH_SIZE,
    coalesce_columns: Sequence[str] | None = None,
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
    update_columns : Sequence[str] | None
        Columns to update when a unique key collision occurs. If None,
        all keys in the row group except index_elements and 'id' are updated.
    index_elements : Sequence[str] | None
        Unique constraint columns for conflict detection (required by SQLite).
        Inferred automatically from table unique constraints if None.
    batch_size : int
        Chunk size for batched execution.
    coalesce_columns : Sequence[str] | None
        Columns that should NEVER be overwritten by NULL if the stored value
        in the database is non-NULL (default: none).

    Returns
    -------
    int
        Number of rows processed.
    """
    if not rows:
        return 0

    # Filter out keys that do not exist on the target model table
    # (e.g. a column that exists in a parsed file but not in the table)
    table_cols = {c.name for c in model.__table__.columns} if hasattr(model, "__table__") else None
    if table_cols is not None:
        filtered_rows = [{k: v for k, v in r.items() if k in table_cols} for r in rows]
        if coalesce_columns:
            coalesce_columns = [c for c in coalesce_columns if c in table_cols]
    else:
        filtered_rows = rows

    bind = session.get_bind()
    dialect_name = bind.dialect.name.lower()

    if index_elements is None:
        index_elements = _infer_index_elements(model)

    index_set = set(index_elements) | {"id"}

    # Group rows by their exact key set so that every row in a chunk carries
    # the same columns. This prevents MySQL error 1054 ("Unknown column 'new.col'
    # in 'field list'") when rows have different sets of keys.
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in filtered_rows:
        key_set = tuple(sorted(row))
        groups.setdefault(key_set, []).append(row)

    total_rows = len(rows)

    # For each group, update only columns that the group carries.
    # Columns that a row does not carry stay untouched in the database.
    for key_set, group_rows in groups.items():
        if update_columns is not None:
            group_update_cols = [c for c in update_columns if c in key_set and c not in index_set]
        else:
            group_update_cols = [c for c in key_set if c not in index_set]

        for start_idx in range(0, len(group_rows), batch_size):
            chunk = group_rows[start_idx : start_idx + batch_size]
            _execute_upsert_chunk(
                session=session,
                model=model,
                chunk=chunk,
                update_columns=group_update_cols,
                index_elements=index_elements,
                coalesce_columns=coalesce_columns,
                dialect_name=dialect_name,
            )

    return total_rows
