"""Unit tests for corporate actions adjustment engine (src/etl/adjust.py)."""

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import AdjFactor, Base, DailyPrice
from src.etl.adjust import (
    adjust_new_rows,
    cumulative_factors,
    detect_events,
    rebuild_adjusted,
)


def _build_series(
    prices: list[float], prev_closes: list[float], symbol: str = "ABC"
) -> pd.DataFrame:
    base_date = date(2024, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "close": prices,
            "prev_close": prev_closes,
        }
    )


def test_detect_split_1_to_5():
    """A 1:5 stock split: close=1000, next day prev_close=200, close=205."""
    df = _build_series([1000.0, 205.0], [1000.0, 200.0])
    events = detect_events(df)
    assert len(events) == 1
    ev = events.iloc[0]
    assert ev["ratio_kind"] == "material"
    assert abs(ev["ratio"] - 0.2) < 1e-4


def test_detect_bonus_1_to_1():
    """A 1:1 bonus issue: close=500, next day prev_close=250, close=255."""
    df = _build_series([500.0, 255.0], [500.0, 250.0])
    events = detect_events(df)
    assert len(events) == 1
    ev = events.iloc[0]
    assert ev["ratio_kind"] == "material"
    assert abs(ev["ratio"] - 0.5) < 1e-4


def test_no_events_on_normal_trading():
    """Normal trading: prev_close equals prior close."""
    df = _build_series([100.0, 102.0, 101.0], [100.0, 100.0, 102.0])
    events = detect_events(df)
    assert len(events) == 0


def test_data_gap_skipped():
    """Trading gap > 7 days must be skipped to avoid false corporate actions."""
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 20)  # 19 day gap
    df = pd.DataFrame(
        {
            "symbol": ["ABC", "ABC"],
            "date": [d1, d2],
            "close": [100.0, 50.0],
            "prev_close": [100.0, 50.0],
        }
    )
    events = detect_events(df)
    assert len(events) == 0


def test_minor_event_not_material():
    """Small dividend adjustment of 0.5% (ratio 0.995) flagged as minor."""
    df = _build_series([100.0, 100.0], [100.0, 99.5])
    events = detect_events(df)
    assert len(events) == 1
    assert events.iloc[0]["ratio_kind"] == "minor"


def test_cumulative_factors_two_events():
    """Two material splits: 1:2 on day 10 (factor 0.5) and 1:2 on day 20 (factor 0.5)."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    events_df = pd.DataFrame(
        [
            {"date": date(2024, 1, 11), "factor": 0.5, "ratio_kind": "material"},
            {"date": date(2024, 1, 21), "factor": 0.5, "ratio_kind": "material"},
        ]
    )
    cum_factors = cumulative_factors(dates, events_df)

    # Before both events (days 0..9): factor = 0.5 * 0.5 = 0.25
    assert abs(cum_factors[0] - 0.25) < 1e-4
    assert abs(cum_factors[9] - 0.25) < 1e-4

    # Between event 1 and event 2 (days 10..19): factor = 0.5
    assert abs(cum_factors[10] - 0.5) < 1e-4
    assert abs(cum_factors[19] - 0.5) < 1e-4

    # On or after event 2 (day 20 onward): factor = 1.0
    assert abs(cum_factors[20] - 1.0) < 1e-4
    assert abs(cum_factors[29] - 1.0) < 1e-4


def test_seed_a_false_split_after_missing_day():
    """Seed A: A single missing day in DB causes prev_close/close_{t-1} to look like a split.

    Dates: 2024-01-02, 03, 05, 08 (2024-01-04 is missing).
    Raw closes: 100, 104, 98, 99. Prev closes: 99, 100, 101, 98.
    Expected: no event detected.
    """
    df = pd.DataFrame(
        {
            "symbol": ["ABC", "ABC", "ABC", "ABC"],
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5), date(2024, 1, 8)],
            "close": [100.0, 104.0, 98.0, 99.0],
            "prev_close": [99.0, 100.0, 101.0, 98.0],
        }
    )
    events = detect_events(df)
    assert len(events) == 0


def test_cross_sectional_guard_market_wide_missing_day():
    """If >15% of active symbols show an event on the same date, guard skips all events."""
    d1 = date(2024, 1, 2)
    d2 = date(2024, 1, 3)
    symbols = [f"SYM{i}" for i in range(10)]
    records = []
    # 3 symbols show a ratio of 0.90 (30% > 15% threshold)
    for i, s in enumerate(symbols):
        records.append({"symbol": s, "date": d1, "close": 100.0, "prev_close": 100.0})
        prev_c = 90.0 if i < 3 else 100.0
        records.append({"symbol": s, "date": d2, "close": 95.0, "prev_close": prev_c})

    df = pd.DataFrame(records)
    events = detect_events(df)
    # Market-wide data glitch on d2 is skipped
    assert events.empty


def test_first_row_ignored():
    """First row of symbol has no prior close so ratio cannot be formed."""
    df = pd.DataFrame(
        {
            "symbol": ["XYZ"],
            "date": [date(2024, 1, 1)],
            "close": [100.0],
            "prev_close": [50.0],  # 50 vs 100 but no prior row
        }
    )
    events = detect_events(df)
    assert events.empty


def test_sqlite_rebuild_adjusted_and_stale_removal():
    """Test full adjusted price rebuild, SQLite upsert, and stale event deletion."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    d3 = date(2024, 1, 3)

    with Session(engine) as session:
        # Pre-insert a stale event for RELIANCE on d1
        session.add(AdjFactor(symbol="RELIANCE", date=d1, factor=0.5, ratio_kind="material"))
        # Insert raw daily prices: RELIANCE has 1:2 split on d2 (close=2000, next prev_close=1000)
        prices = [
            DailyPrice(
                symbol="RELIANCE",
                date=d1,
                open=2000.0,
                high=2050.0,
                low=1950.0,
                close=2000.0,
                prev_close=2000.0,
                volume=10000,
            ),
            DailyPrice(
                symbol="RELIANCE",
                date=d2,
                open=1000.0,
                high=1050.0,
                low=980.0,
                close=1020.0,
                prev_close=1000.0,
                volume=20000,
            ),
            DailyPrice(
                symbol="RELIANCE",
                date=d3,
                open=1020.0,
                high=1040.0,
                low=1010.0,
                close=1030.0,
                prev_close=1020.0,
                volume=15000,
            ),
            DailyPrice(
                symbol="TCS",
                date=d1,
                open=3500.0,
                high=3550.0,
                low=3480.0,
                close=3500.0,
                prev_close=3500.0,
                volume=5000,
            ),
            DailyPrice(
                symbol="TCS",
                date=d2,
                open=3510.0,
                high=3560.0,
                low=3490.0,
                close=3520.0,
                prev_close=3500.0,
                volume=6000,
            ),
        ]
        session.add_all(prices)
        session.commit()

        # Rebuild adjusted prices
        updated = rebuild_adjusted(session=session)
        session.commit()
        assert updated == 5

        # Verify stale event on d1 was removed and newly detected event on d2 was added
        stored_factors = session.scalars(select(AdjFactor).order_by(AdjFactor.date)).all()
        assert len(stored_factors) == 1
        assert stored_factors[0].symbol == "RELIANCE"
        assert stored_factors[0].date == d2
        assert float(stored_factors[0].factor) == 0.5

        # Verify adjusted prices for RELIANCE
        rel_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "RELIANCE", DailyPrice.date == d1)
        ).one()
        assert float(rel_d1.adj_close) == 1000.0  # 2000 * 0.5
        assert rel_d1.adj_volume == 20000  # 10000 / 0.5

        rel_d2 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "RELIANCE", DailyPrice.date == d2)
        ).one()
        assert float(rel_d2.adj_close) == 1020.0  # Factor after event is 1.0

        # Verify TCS had no event so adj_close == close
        tcs_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "TCS", DailyPrice.date == d1)
        ).one()
        assert float(tcs_d1.adj_close) == 3500.0

        # Idempotency: second rebuild produces identical state
        updated_2 = rebuild_adjusted(session=session)
        session.commit()
        assert updated_2 == 5


def test_sqlite_adjust_new_rows_incremental():
    """Test adjust_new_rows: fills adj_* = raw when no event, rebuilds when new event."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    with Session(engine) as session:
        # 1. Normal trading day with no event
        new_df = pd.DataFrame(
            [
                {
                    "symbol": "INFY",
                    "date": d1,
                    "open": 1500.0,
                    "high": 1520.0,
                    "low": 1490.0,
                    "close": 1510.0,
                    "prev_close": 1500.0,
                    "volume": 50000,
                },
                {
                    "symbol": "INFY",
                    "date": d2,
                    "open": 1515.0,
                    "high": 1530.0,
                    "low": 1505.0,
                    "close": 1525.0,
                    "prev_close": 1510.0,
                    "volume": 60000,
                },
            ]
        )
        for r in new_df.to_dict("records"):
            session.add(DailyPrice(**r))
        session.commit()

        # Adjust new rows
        count = adjust_new_rows(new_df, session=session)
        session.commit()
        assert count == 2

        infy_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "INFY", DailyPrice.date == d1)
        ).one()
        assert float(infy_d1.adj_close) == 1510.0
