"""Tests for detecting splits and bonuses from price jumps (issue #48).

Real NSE files give PREV_CLOSE = the previous day's UNADJUSTED close, so comparing
PREV_CLOSE with the previous close never sees a split. These tests use the real
numbers from the database (RELIANCE 2017, HDFCBANK 2019, BAJFINANCE 2025).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.db.models import AdjFactor, Base, DailyPrice
from src.etl.adjust import (
    detect_all_events,
    detect_price_jump_events,
    rebuild_adjusted,
)


def _series(symbol: str, closes: list[float], start: date = date(2024, 1, 1)) -> pd.DataFrame:
    """Daily rows for one symbol. prev_close is the raw previous close, like real NSE files."""
    dates = [start + timedelta(days=i) for i in range(len(closes))]
    prev = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "close": closes,
            "prev_close": prev,
            "volume": 1000,
        }
    )


def test_reliance_2017_bonus_1_to_1_is_detected():
    """Real numbers: close 1645.40 then 818.10 (ratio 2.01) is a 1:1 bonus, factor 0.5."""
    df = _series("RELIANCE", [1632.6, 1645.4, 818.1, 816.9])
    events = detect_price_jump_events(df)
    confirmed = events[events["status"] == "confirmed"]
    assert len(confirmed) == 1
    row = confirmed.iloc[0]
    assert row["date"] == date(2024, 1, 3)  # the ex-date is the first day at the new price
    assert row["factor"] == 0.5
    assert row["ratio_kind"] == "material"


def test_hdfcbank_2019_split_2_to_1_is_detected():
    """Real numbers: 2187.75 then 1101.05 (ratio 1.987) snaps to a clean 2:1 factor 0.5."""
    df = _series("HDFCBANK", [2211.35, 2187.75, 1101.05, 1199.6])
    confirmed = detect_price_jump_events(df).query("status == 'confirmed'")
    assert len(confirmed) == 1
    assert confirmed.iloc[0]["factor"] == 0.5


def test_bajfinance_2025_bonus_plus_split_ratio_10():
    """Real numbers: 9331 then 938 (ratio 9.95) snaps to 10, factor 0.1."""
    df = _series("BAJFINANCE", [9300.0, 9331.0, 938.0, 940.0])
    confirmed = detect_price_jump_events(df).query("status == 'confirmed'")
    assert len(confirmed) == 1
    assert confirmed.iloc[0]["factor"] == 0.1


def test_big_price_rise_is_never_applied_automatically():
    """PNB 2017 and YES Bank 2020 rallied about 45 percent: not reverse splits."""
    rally = _series("PNB", [100.0, 100.0, 146.0, 147.0])
    events = detect_price_jump_events(rally)
    assert len(events) == 1
    assert events.iloc[0]["status"] == "review"
    assert events.iloc[0]["ratio_kind"] == "review"

    # Even a perfect 10x rise (a possible 1-for-10 consolidation) only goes to the review list.
    tenfold = detect_price_jump_events(_series("CONSOL", [10.0, 10.2, 101.0, 102.0]))
    assert (tenfold["status"] == "review").all()


def test_review_row_suggests_the_nearest_clean_factor():
    """A split whose price also moved on the ex-date: ratio 10.64 is 6.4 percent from a clean 10."""
    df = _series("NYKALIKE", [100.0, 100.0, 9.4, 9.5])
    row = detect_price_jump_events(df).iloc[0]
    assert row["status"] == "review"  # just outside the 6 percent tolerance: not applied
    assert round(row["suggested_factor"], 4) == 0.1


def test_normal_moves_and_circuit_limit_drop_are_not_applied():
    """A -10% day is ignored; a -20% circuit-limit drop is only listed for review."""
    ten_percent = _series("DROP10", [100.0, 90.0, 91.0])
    assert detect_price_jump_events(ten_percent).empty

    twenty_percent = _series("DROP20", [100.0, 80.0, 81.0])
    events = detect_price_jump_events(twenty_percent)
    assert (events["status"] == "review").all()
    assert not (events["status"] == "confirmed").any()


def test_big_drop_that_is_not_a_clean_ratio_goes_to_review():
    """A 45% crash (ratio 1.82, between clean 1.67 and 2.0) must not be adjusted automatically."""
    df = _series("CRASH", [100.0, 55.0, 56.0])
    events = detect_price_jump_events(df)
    assert len(events) == 1
    assert events.iloc[0]["status"] == "review"


def test_missing_previous_trading_day_is_skipped():
    """A price halving after a data gap is not trusted (the gap may hide days of moves)."""
    df = _series("GAPPY", [100.0, 100.0, 50.0])
    all_days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    df = df.drop(index=1)  # symbol has no row on Jan 2, but the market traded that day
    assert detect_price_jump_events(df, global_trading_days=all_days).empty


def test_market_wide_halving_on_one_date_is_a_data_problem():
    """If many symbols halve on the same date, treat it as bad data, not splits."""
    frames = [_series(f"S{i}", [100.0, 100.0, 50.0]) for i in range(10)]
    df = pd.concat(frames, ignore_index=True)
    assert detect_price_jump_events(df, total_db_symbols=10).empty


def test_detection_uses_no_future_prices():
    """No look-ahead: an event on day t must not change if later prices change."""
    base = _series("NOLOOK", [100.0, 100.0, 50.0, 51.0, 52.0])
    changed = _series("NOLOOK", [100.0, 100.0, 50.0, 500.0, 5.0])
    first = detect_price_jump_events(base.iloc[:3])
    again = detect_price_jump_events(changed.iloc[:3])
    pd.testing.assert_frame_equal(first.reset_index(drop=True), again.reset_index(drop=True))
    assert first.iloc[0]["date"] == date(2024, 1, 3)


def test_volume_multiple_uses_only_past_volumes():
    """The report column compares ex-date volume with the median of the days BEFORE it."""
    df = _series("VOLS", [100.0, 100.0, 100.0, 50.0])
    df["volume"] = [1000, 1000, 1000, 5000]
    row = detect_price_jump_events(df).iloc[0]
    assert row["volume_multiple"] == 5.0


def test_exact_prev_close_event_is_not_applied_twice():
    """If PREV_CLOSE already shows the split, the price-jump event is dropped (no double)."""
    df = _series("EXACT", [100.0, 100.0, 50.0])
    df.loc[2, "prev_close"] = 50.0  # NSE adjusted PREV_CLOSE for the split on this day
    applied, _review = detect_all_events(df)
    assert len(applied) == 1
    assert applied.iloc[0]["source"] == "prev_close"
    assert applied.iloc[0]["factor"] == 0.5


def test_rebuild_adjusted_makes_the_history_smooth_across_a_bonus():
    """End to end on SQLite: adj_close before the bonus is halved, nothing after changes."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    closes = [1632.6, 1645.4, 818.1, 816.9]
    start = date(2017, 9, 5)
    with Session(engine) as session:
        for i, close in enumerate(closes):
            session.add(
                DailyPrice(
                    symbol="RELIANCE",
                    date=start + timedelta(days=i),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    prev_close=closes[max(i - 1, 0)],
                    volume=1000,
                )
            )
        session.commit()

        rebuild_adjusted(session=session)
        session.commit()

        rows = session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
        adjusted = [float(r.adj_close) for r in rows]
        assert adjusted == [816.3, 822.7, 818.1, 816.9]
        factors = session.scalars(select(AdjFactor)).all()
        assert len(factors) == 1
        assert float(factors[0].factor) == 0.5
