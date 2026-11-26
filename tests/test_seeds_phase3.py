"""Reproduction seeds for Phase 3 review gaps (Seeds D and E).

These tests reproduce bugs and gaps identified from Phase 2:
- Seed D: incremental adjustment must see a new official event when new_df holds 1 row per symbol.
- Seed E: cross-sectional guard in rebuild_adjusted suppresses genuine splits in subsets.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice
from src.etl.adjust import adjust_new_rows, rebuild_adjusted
from tests.official_actions import add_official_action


def test_seed_d_newest_day_event_missed_incremental():
    """Seed D: adjust_new_rows with only one new row per symbol must still apply a new split.

    Example: DB has raw close=1000 on d1. The new row on d2 is the ex-date of an official
    1:5 split. new_df holds only 1 row for the symbol, so the symbol's last stored row (d1)
    must be read from the DB to know there are prices before the event.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    with Session(engine) as session:
        # Prior day stored in database
        prior_row = DailyPrice(
            symbol="INFY",
            date=d1,
            open=990.0,
            high=1010.0,
            low=980.0,
            close=1000.0,
            prev_close=990.0,
            volume=50000,
            adj_open=990.0,
            adj_high=1010.0,
            adj_low=980.0,
            adj_close=1000.0,
            adj_volume=50000,
        )
        session.add(prior_row)
        add_official_action(session, "INFY", d2, factor=0.2)
        session.commit()

        # Incremental new row (only 1 day: d2)
        new_df = pd.DataFrame(
            [
                {
                    "symbol": "INFY",
                    "date": d2,
                    "open": 198.0,
                    "high": 205.0,
                    "low": 195.0,
                    "close": 200.0,
                    "prev_close": 200.0,  # Split 1:5 ratio 200 / 1000 = 0.2
                    "volume": 250000,
                }
            ]
        )
        # Add to DB table as loader would
        for r in new_df.to_dict("records"):
            session.add(DailyPrice(**r))
        session.commit()

        # Run incremental adjustment
        adjust_new_rows(new_df, session=session, engine=engine)
        session.commit()

        # Check d1 row: should be backward adjusted by 0.2 (1000 * 0.2 = 200.0)
        d1_row = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "INFY", DailyPrice.date == d1)
        ).one()
        assert d1_row.adj_close is not None and abs(float(d1_row.adj_close) - 200.0) < 1e-3, (
            f"Expected d1 adj_close to be 200.0 after split, got {d1_row.adj_close}"
        )


def test_seed_e_subset_guard_cross_sectional_denominator():
    """Seed E: rebuild_adjusted(symbols=[...]) with split suppressed by subset denominator.

    If 6 symbols out of 50 in the DB experience a split on the same date, 6/50 = 12% <= 15%.
    Legacy rebuild_adjusted computed denominator as len(subset) = 6, so 6/6 = 100% > 15%,
    falsely suppressing all 6 corporate actions!
    The denominator must be the total number of symbols in the database.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)

    with Session(engine) as session:
        # Create a database with 50 symbols
        all_symbols = [f"SYM_{i:02d}" for i in range(50)]
        target_six = all_symbols[:6]

        for sym in all_symbols:
            if sym in target_six:
                # Genuine 1:2 split on d2: close=100 on d1, close=50, prev_close=50 on d2
                r1 = DailyPrice(
                    symbol=sym,
                    date=d1,
                    open=100.0,
                    high=102.0,
                    low=98.0,
                    close=100.0,
                    prev_close=100.0,
                    volume=10000,
                )
                r2 = DailyPrice(
                    symbol=sym,
                    date=d2,
                    open=50.0,
                    high=52.0,
                    low=48.0,
                    close=50.0,
                    prev_close=50.0,
                    volume=20000,
                )
            else:
                # Normal trading: close=100 on d1, close=100, prev_close=100 on d2
                r1 = DailyPrice(
                    symbol=sym,
                    date=d1,
                    open=100.0,
                    high=102.0,
                    low=98.0,
                    close=100.0,
                    prev_close=100.0,
                    volume=10000,
                )
                r2 = DailyPrice(
                    symbol=sym,
                    date=d2,
                    open=100.0,
                    high=102.0,
                    low=98.0,
                    close=100.0,
                    prev_close=100.0,
                    volume=10000,
                )
            session.add(r1)
            session.add(r2)
            if sym in target_six:
                add_official_action(session, sym, d2, factor=0.5)
        session.commit()

        # Rebuild only the 6 targeted symbols
        rebuild_adjusted(symbols=target_six, session=session, engine=engine)
        session.commit()

        # Check that target_six symbols were adjusted (d1 adj_close should be 50.0, not 100.0)
        sample = session.scalars(
            select(DailyPrice).where(DailyPrice.symbol == target_six[0], DailyPrice.date == d1)
        ).one()
        assert sample.adj_close is not None and abs(float(sample.adj_close) - 50.0) < 1e-3, (
            f"Expected d1 adj_close to be 50.0, got {sample.adj_close}"
        )
