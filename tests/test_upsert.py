"""Unit tests for dialect-agnostic upsert helper (src/db/upsert.py)."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice
from src.db.upsert import upsert_rows


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
