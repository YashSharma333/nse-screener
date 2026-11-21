"""Unit tests for dialect-agnostic upsert helper (src/db/upsert.py)."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice, Security, SymbolChange
from src.db.upsert import _build_upsert_statement, _infer_index_elements, upsert_rows


def test_upsert_rows_sqlite_insert_and_update():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    initial_rows = [
        {"symbol": "RELIANCE", "date": d1, "close": 2500.0, "volume": 1000},
        {"symbol": "RELIANCE", "date": d2, "close": 2550.0, "volume": 1200},
    ]

    with Session(engine) as session:
        count = upsert_rows(session, DailyPrice, initial_rows)
        session.commit()
        assert count == 2

    with Session(engine) as session:
        stored = session.query(DailyPrice).order_by(DailyPrice.date).all()
        assert len(stored) == 2
        assert float(stored[0].close) == 2500.0
        assert stored[0].volume == 1000

    # Upsert updated close and new volume on d2, plus new row on d3
    d3 = date(2024, 1, 3)
    update_rows = [
        {"symbol": "RELIANCE", "date": d2, "close": 2600.0, "volume": 1500},
        {"symbol": "RELIANCE", "date": d3, "close": 2650.0, "volume": 2000},
    ]

    with Session(engine) as session:
        count = upsert_rows(session, DailyPrice, update_rows)
        session.commit()
        assert count == 2

    with Session(engine) as session:
        stored = session.query(DailyPrice).order_by(DailyPrice.date).all()
        assert len(stored) == 3
        assert stored[1].date == d2
        assert float(stored[1].close) == 2600.0
        assert stored[1].volume == 1500
        assert stored[2].date == d3
        assert float(stored[2].close) == 2650.0


def test_upsert_rows_never_overwrites_non_null_coalesce_column_with_null():
    """Verify that upsert never overwrites an existing non-NULL coalesce column with NULL."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    d = date(2024, 1, 1)

    with Session(engine) as session:
        upsert_rows(
            session,
            DailyPrice,
            [{"symbol": "TCS", "date": d, "close": 3500.0, "isin": "INE467B01029"}],
            coalesce_columns=("isin",),
        )
        session.commit()

    with Session(engine) as session:
        stored = session.query(DailyPrice).filter_by(symbol="TCS", date=d).one()
        assert stored.isin == "INE467B01029"

    # Upsert with isin = None -> isin MUST remain 'INE467B01029'
    with Session(engine) as session:
        upsert_rows(
            session,
            DailyPrice,
            [{"symbol": "TCS", "date": d, "close": 3550.0, "isin": None}],
            coalesce_columns=("isin",),
        )
        session.commit()

    with Session(engine) as session:
        stored = session.query(DailyPrice).filter_by(symbol="TCS", date=d).one()
        assert float(stored.close) == 3550.0
        assert stored.isin == "INE467B01029"


def test_upsert_rows_sqlite_heterogeneous_key_sets_and_none_values():
    """Verify SQLite handling of mixed key sets, None values, and untouched columns."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    d = date(2024, 1, 1)

    # Heterogeneous batch: some rows contain series, some omit it, some have None
    mixed_rows = [
        {"symbol": "INFY", "name": "Infosys", "status": "active", "series": "EQ"},
        {
            "symbol": "TCS",
            "name": "Tata Consultancy Services",
            "status": "active",
            # series omitted
        },
        {"symbol": "WIPRO", "name": "Wipro", "status": "suspended", "series": None},
    ]

    with Session(engine) as session:
        count = upsert_rows(session, Security, mixed_rows)
        session.commit()
        assert count == 3

    with Session(engine) as session:
        infy = session.query(Security).filter_by(symbol="INFY").one()
        tcs = session.query(Security).filter_by(symbol="TCS").one()
        wipro = session.query(Security).filter_by(symbol="WIPRO").one()

        assert infy.series == "EQ"
        assert tcs.series is None
        assert wipro.series is None

    # Upsert INFY with a row that lacks series: the stored value MUST NOT be overwritten
    update_row = [{"symbol": "INFY", "name": "Infosys Limited", "status": "active"}]
    with Session(engine) as session:
        upsert_rows(session, Security, update_row)
        session.commit()

    with Session(engine) as session:
        infy = session.query(Security).filter_by(symbol="INFY").one()
        assert infy.name == "Infosys Limited"
        assert infy.series == "EQ"

    # Verify isin coalesce with heterogeneous daily_price rows
    daily_rows = [
        {
            "symbol": "INFY",
            "date": d,
            "close": 1500.0,
            "isin": "INE009A01021",
        },
        {
            "symbol": "TCS",
            "date": d,
            "close": 3600.0,
            # isin omitted
        },
    ]
    with Session(engine) as session:
        upsert_rows(session, DailyPrice, daily_rows, coalesce_columns=("isin",))
        session.commit()

    # Update INFY with isin=None -> coalesce must keep 'INE009A01021'
    with Session(engine) as session:
        upsert_rows(
            session,
            DailyPrice,
            [{"symbol": "INFY", "date": d, "close": 1520.0, "isin": None}],
            coalesce_columns=("isin",),
        )
        session.commit()

    with Session(engine) as session:
        stored_infy = session.query(DailyPrice).filter_by(symbol="INFY", date=d).one()
        assert float(stored_infy.close) == 1520.0
        assert stored_infy.isin == "INE009A01021"


def _parse_mysql_insert_and_update_columns(sql: str) -> tuple[list[str], list[str]]:
    """Parse a compiled MySQL INSERT [IGNORE] ... ON DUPLICATE KEY UPDATE statement.

    Returns (insert_columns, update_target_columns).
    """
    match_insert = re.search(
        r"INSERT (?:IGNORE )?INTO\s+\S+\s*\((.*?)\)", sql, re.IGNORECASE | re.DOTALL
    )
    assert match_insert, f"Could not find INSERT columns in: {sql}"
    insert_cols = [c.strip().strip("`") for c in match_insert.group(1).split(",")]

    update_cols = []
    if "ON DUPLICATE KEY UPDATE" in sql:
        update_part = sql.split("ON DUPLICATE KEY UPDATE")[1]
        # Match target column names immediately preceding '='
        update_cols = [col.strip("`") for col in re.findall(r"(\b\w+\b)\s*=", update_part)]

    return insert_cols, update_cols


def test_upsert_rows_mysql_compile_statement_columns():
    """No-server compile test: verify that for Security, SymbolChange, and DailyPrice,

    every column in ON DUPLICATE KEY UPDATE is projected in the INSERT column list.
    """
    mysql_dialect = sa.dialects.mysql.dialect()

    test_groups: list[tuple[Any, list[dict[str, Any]]]] = [
        # Security: minimal keys vs full keys vs None values
        (
            Security,
            [
                {"symbol": "INFY", "name": "Infosys", "status": "active"},
                {"symbol": "TCS", "name": "TCS", "status": "active", "series": "EQ"},
                {
                    "symbol": "WIPRO",
                    "name": "Wipro",
                    "status": "active",
                    "series": None,
                    "isin": "INE075A01022",
                },
            ],
        ),
        # SymbolChange: minimal keys vs optional company
        (
            SymbolChange,
            [
                {"old_symbol": "AAA", "new_symbol": "BBB", "effective_date": "2024-05-01"},
                {
                    "old_symbol": "CCC",
                    "new_symbol": "DDD",
                    "effective_date": "2024-05-01",
                    "company": None,
                },
                {
                    "old_symbol": "EEE",
                    "new_symbol": "FFF",
                    "effective_date": "2024-05-01",
                    "company": "Example Ltd",
                },
            ],
        ),
        # DailyPrice: prices with and without an isin
        (
            DailyPrice,
            [
                {"symbol": "INFY", "date": "d", "open": 100.0, "close": 102.0, "volume": 1000},
                {
                    "symbol": "TCS",
                    "date": "d",
                    "close": 200.0,
                    "volume": 2000,
                    "isin": "INE467B01029",
                },
                {
                    "symbol": "WIPRO",
                    "date": "d",
                    "close": 300.0,
                    "adj_close": 300.0,
                    "isin": None,
                },
            ],
        ),
    ]

    for model, rows in test_groups:
        index_elements = _infer_index_elements(model)
        index_set = set(index_elements) | {"id"}

        # Simulate grouping by key set as done in upsert_rows
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in rows:
            key_set = tuple(sorted(row))
            groups.setdefault(key_set, []).append(row)

        for key_set in groups:
            group_keys = list(key_set)
            group_update_cols = [c for c in group_keys if c not in index_set]

            stmt = _build_upsert_statement(
                model=model,
                update_columns=group_update_cols,
                index_elements=index_elements,
                coalesce_columns=("isin",),
                dialect_name="mysql",
            )
            compiled = stmt.compile(dialect=mysql_dialect, column_keys=group_keys)
            sql = str(compiled)

            insert_cols, update_cols = _parse_mysql_insert_and_update_columns(sql)

            for uc in update_cols:
                assert uc in insert_cols, (
                    f"Model {model.__name__} (keys={group_keys}): column {uc!r} in UPDATE "
                    f"is not in INSERT columns {insert_cols} (would cause MySQL 1054 error)"
                )


def is_mysql_reachable() -> bool:
    """Check if a live MySQL server is reachable using environment credentials."""
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("MYSQL_DB", "nse_screener")
    url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"
    try:
        eng = create_engine(url, connect_args={"connect_timeout": 1})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _get_mysql_session() -> Session:
    """Create a Session connected to the MySQL database."""
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("MYSQL_DB", "nse_screener")
    url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"
    eng = create_engine(url)
    return Session(eng)


@pytest.mark.mysql
@pytest.mark.skipif(not is_mysql_reachable(), reason="MySQL not reachable")
def test_upsert_rows_mysql_integration():
    """Real-MySQL integration test running against a live MySQL 8.0 server in CI."""
    d = date(2024, 1, 1)

    # 1. Security: heterogeneous keys, None values, and untouched columns
    sec_rows = [
        {
            "symbol": "T_UPSERT_1",
            "name": "Test Upsert 1",
            "series": "EQ",
            "isin": "INE001A01011",
            "status": "active",
        },
        {
            "symbol": "T_UPSERT_2",
            "name": "Test Upsert 2",
            "series": "BE",
            # isin omitted
        },
        {
            "symbol": "T_UPSERT_3",
            "name": "Test Upsert 3",
            "status": "suspended",
            "isin": None,
        },
    ]

    with _get_mysql_session() as session:
        try:
            upsert_rows(session, Security, sec_rows, index_elements=["symbol"])
            session.commit()

            # Verify stored rows
            s1 = session.query(Security).filter_by(symbol="T_UPSERT_1").one()
            assert s1.isin == "INE001A01011"

            # Update row omitting isin -> stored value must remain untouched
            upsert_rows(
                session,
                Security,
                [
                    {
                        "symbol": "T_UPSERT_1",
                        "name": "Test Upsert 1 Updated",
                        "series": "EQ",
                        "status": "active",
                    }
                ],
                index_elements=["symbol"],
            )
            session.commit()

            s1 = session.query(Security).filter_by(symbol="T_UPSERT_1").one()
            assert s1.name == "Test Upsert 1 Updated"
            assert s1.isin == "INE001A01011"

            # 2. DailyPrice: mixed keys and isin coalesce on MySQL
            price_rows = [
                {
                    "symbol": "T_UPSERT_1",
                    "date": d,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 10000,
                    "prev_close": 100.0,
                    "isin": "INE123456789",
                },
                {
                    "symbol": "T_UPSERT_2",
                    "date": d,
                    "open": 200.0,
                    "high": 205.0,
                    "low": 198.0,
                    "close": 202.0,
                    "volume": 5000,
                    "prev_close": 200.0,
                    # isin omitted
                },
            ]
            upsert_rows(
                session,
                DailyPrice,
                price_rows,
                index_elements=["symbol", "date"],
                coalesce_columns=("isin",),
            )
            session.commit()

            # Update with isin=None -> coalesce must keep 'INE123456789' on MySQL
            upsert_rows(
                session,
                DailyPrice,
                [
                    {
                        "symbol": "T_UPSERT_1",
                        "date": d,
                        "close": 104.0,
                        "isin": None,
                    }
                ],
                index_elements=["symbol", "date"],
                coalesce_columns=("isin",),
            )
            session.commit()

            p1 = session.query(DailyPrice).filter_by(symbol="T_UPSERT_1", date=d).one()
            assert float(p1.close) == 104.0
            assert p1.isin == "INE123456789"

        finally:
            # Clean up test rows
            session.query(Security).filter(
                Security.symbol.in_(["T_UPSERT_1", "T_UPSERT_2", "T_UPSERT_3"])
            ).delete(synchronize_session=False)
            session.query(DailyPrice).filter(
                DailyPrice.symbol.in_(["T_UPSERT_1", "T_UPSERT_2", "T_UPSERT_3"])
            ).delete(synchronize_session=False)
            session.commit()
