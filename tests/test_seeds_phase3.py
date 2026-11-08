"""Reproduction seeds for Phase 3 review gaps (Seeds D, E, F, G).

These tests reproduce bugs and gaps identified from Phase 2:
- Seed D: detect_events fails in incremental mode when new_df holds only 1 row per symbol.
- Seed E: cross-sectional guard in rebuild_adjusted suppresses genuine splits in subsets.
- Seed F: condition helpers using .shift() across rows in snapshot instead of
  raising MissingIndicatorError.
- Seed G: rename equivalence between HMM and RSI Momentum.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.computation.indicators.calculator import TechnicalCalculator
from src.db.models import Base, DailyPrice
from src.etl.adjust import adjust_new_rows, rebuild_adjusted
from src.screeners.base import MissingIndicatorError
from src.screeners.conditions.crossover import cond_golden_cross
from src.screeners.conditions.price import cond_sma_trending_up
from src.screeners.registry import get_screener


def test_seed_d_newest_day_event_missed_incremental():
    """Seed D: adjust_new_rows with only one new row per symbol misses corporate action.

    Example: DB has raw close=1000 on d1. New row on d2 has close=200, prev_close=200 (1:5 split).
    Because new_df has only 1 row for the symbol, close.shift(1) is NaN and detect_events sees 0.
    The incremental path must compare with the symbol's last stored close from the DB.
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


def test_seed_f_shift_fallbacks_raise_missing_indicator_error():
    """Seed F: Condition helpers must raise MissingIndicatorError instead of fallback shift."""
    # A snapshot DataFrame with 3 different symbols on the same date
    snapshot_df = pd.DataFrame(
        {
            "symbol": ["INFY", "RELIANCE", "TCS"],
            "date": [date(2024, 1, 1)] * 3,
            "close": [1500.0, 2500.0, 3500.0],
            # Note: 'golden_cross', 'sma_50', etc. are missing
        }
    )

    with pytest.raises(MissingIndicatorError) as exc_info_cross:
        cond_golden_cross(snapshot_df)
    assert (
        "golden_cross" in exc_info_cross.value.missing_columns
        or "sma_50" in exc_info_cross.value.missing_columns
    )

    with pytest.raises(MissingIndicatorError) as exc_info_sma:
        cond_sma_trending_up(snapshot_df, sma_col="sma_50", periods=5)
    assert (
        "sma_50" in exc_info_sma.value.missing_columns
        or "sma_50_slope_5d" in exc_info_sma.value.missing_columns
    )


def _generate_synthetic_400d_dataset() -> pd.DataFrame:
    """Generate fixed deterministic multi-symbol 400-day dataset."""
    rng = np.random.default_rng(42)
    symbols = ["SYM_A", "SYM_B", "SYM_C", "SYM_D"]
    dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(400)]

    records = []
    for sym in symbols:
        price = 100.0
        for d in dates:
            ret = rng.normal(0.0005, 0.015)
            price = max(1.0, price * (1.0 + ret))
            high = price * (1.0 + abs(rng.normal(0, 0.005)))
            low = price * (1.0 - abs(rng.normal(0, 0.005)))
            vol = int(max(10_000, rng.normal(300_000, 100_000)))
            records.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": price,
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": vol,
                }
            )
    return pd.DataFrame(records)


def test_seed_g_rename_equivalence_hmm_vs_rsi_momentum():
    """Seed G: New rsi_momentum column and screener produce identical results to old HMM."""
    df = _generate_synthetic_400d_dataset()
    res = TechnicalCalculator.apply_all_indicators(df)

    # After rename G1:
    assert "rsi_momentum" in res.columns, "rsi_momentum must be present in calculated indicators"
    assert "rsi_9" in res.columns, "rsi_9 helper column must be present in calculated indicators"
    assert "rsi_9_wma_21" in res.columns, (
        "rsi_9_wma_21 helper column must be present in calculated indicators"
    )

    screener = get_screener("rsi_momentum")
    screener_res = screener.run(res)

    # The boolean mask of the screener should match rsi_momentum == True
    expected_mask = res["rsi_momentum"].fillna(False).astype(bool)
    expected_symbols_dates = res.loc[expected_mask, ["symbol", "date"]].reset_index(drop=True)
    actual_symbols_dates = screener_res[["symbol", "date"]].reset_index(drop=True)

    pd.testing.assert_frame_equal(expected_symbols_dates, actual_symbols_dates)
