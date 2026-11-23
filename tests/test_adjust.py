"""Unit tests for the corporate actions adjustment engine (src/etl/adjust.py)."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from config import settings
from src.db.models import AdjFactor, Base, DailyPrice
from src.etl.adjust import adjust_new_rows, cumulative_factors, rebuild_adjusted
from src.etl.daily_update import run_daily_update
from tests.official_actions import add_official_action, add_placeholder_action


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


def test_sqlite_rebuild_adjusted_and_stale_removal():
    """Full adjusted price rebuild from an official event, SQLite upsert, stale event deletion."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    d3 = date(2024, 1, 3)

    with Session(engine) as session:
        # Pre-insert a stale event for RELIANCE on d1
        session.add(AdjFactor(symbol="RELIANCE", date=d1, factor=0.5, ratio_kind="material"))
        # The official list says RELIANCE had a 1:2 split with ex-date d2
        add_official_action(session, "RELIANCE", d2, factor=0.5)
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

        updated = rebuild_adjusted(session=session)
        session.commit()
        assert updated == 5

        # The stale event on d1 was removed and the official event on d2 was added
        stored_factors = session.scalars(select(AdjFactor).order_by(AdjFactor.date)).all()
        assert len(stored_factors) == 1
        assert stored_factors[0].symbol == "RELIANCE"
        assert stored_factors[0].date == d2
        assert float(stored_factors[0].factor) == 0.5

        rel_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "RELIANCE", DailyPrice.date == d1)
        ).one()
        assert float(rel_d1.adj_close) == 1000.0  # 2000 * 0.5
        assert rel_d1.adj_volume == 20000  # 10000 / 0.5

        rel_d2 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "RELIANCE", DailyPrice.date == d2)
        ).one()
        assert float(rel_d2.adj_close) == 1020.0  # Factor after event is 1.0

        # TCS has no official event so adj_close == close
        tcs_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "TCS", DailyPrice.date == d1)
        ).one()
        assert float(tcs_d1.adj_close) == 3500.0

        # Idempotency: second rebuild modifies 0 rows (changed/NULL filter)
        updated_2 = rebuild_adjusted(session=session)
        session.commit()
        assert updated_2 == 0


def test_sqlite_adjust_new_rows_incremental():
    """adjust_new_rows fills adj_* = raw when the official list has no new event."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    with Session(engine) as session:
        add_placeholder_action(session)  # official table is filled, but nothing for INFY
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

        count = adjust_new_rows(new_df, session=session)
        session.commit()
        assert count == 2

        infy_d1 = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "INFY", DailyPrice.date == d1)
        ).one()
        assert float(infy_d1.adj_close) == 1510.0


def _price_row(symbol: str, day: date, close: float, **extra) -> DailyPrice:
    """One raw price row; adj_* stay NULL unless passed in ``extra``."""
    return DailyPrice(
        symbol=symbol, date=day, open=close, high=close, low=close, close=close,
        prev_close=close, volume=1000, **extra
    )  # fmt: skip


def test_step_7_fills_rows_with_missing_adjusted_prices(tmp_path: Path) -> None:
    """Rows left with NULL adj_* by an earlier failed run are fixed even with no new dates (B4)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'adj.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        add_placeholder_action(session)
        session.add_all(_price_row("INFY", date(2024, 1, d), 1500.0 + d) for d in (1, 2, 3))
        session.commit()

        stubs = {i: (lambda: {}) for i in range(1, 10) if i != 7}  # only the real step 7 runs
        run_daily_update(session=session, today=date(2024, 1, 4), step_handlers=stubs)

        rows = session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
        assert [float(r.adj_close) for r in rows] == [1501.0, 1502.0, 1503.0]

        # A second run has nothing left to fix (idempotent): no NULL adj_close remains.
        assert session.scalars(select(DailyPrice).where(DailyPrice.adj_close.is_(None))).all() == []


def test_partial_adjust_keeps_other_symbols_in_reports() -> None:
    """Adjusting one symbol must not wipe the other symbols from adj_events_review.csv (B5)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    reports = settings.REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)
    review_csv = reports / "adj_events_review.csv"
    pd.DataFrame([{"symbol": "TCS", "date": "2023-05-02", "factor": 0.5}]).to_csv(
        review_csv, index=False
    )

    with Session(engine) as session:
        add_official_action(session, "INFY", date(2024, 1, 2), factor=0.5)
        rows = [_price_row("INFY", date(2024, 1, d), 1500.0) for d in (1, 2)]
        session.add_all(rows)
        session.commit()
        new_df = pd.DataFrame([{"symbol": "INFY", "date": date(2024, 1, 2), "close": 1500.0}])
        adjust_new_rows(new_df, session=session)  # INFY has a new split: partial rebuild

    saved = pd.read_csv(review_csv)
    assert sorted(saved["symbol"]) == ["INFY", "TCS"]
