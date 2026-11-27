"""Tests for the price-jump REVIEW report (find_unexplained_jumps).

Prices are never adjusted from jumps any more (task CA2): a big overnight move that no
applied official event explains is only listed for the owner. The numbers are real ones
from the database (RELIANCE 2017 bonus, PNB 2017 rally).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.etl.adjust import find_unexplained_jumps

NO_EVENTS = pd.DataFrame(columns=["symbol", "date", "factor", "ratio_kind", "source", "kinds"])


def _series(symbol: str, closes: list[float], start: date = date(2024, 1, 1)) -> pd.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(closes))]
    return pd.DataFrame({"symbol": symbol, "date": dates, "close": closes, "volume": 1000})


def _event(symbol: str, day: date, factor: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "date": day,
                "factor": factor,
                "ratio_kind": "material",
                "source": "official",
                "kinds": "BONUS",
            }
        ]
    )


def test_unexplained_halving_is_listed_with_a_reason():
    """RELIANCE-like 1:1 bonus that NSE's list does not contain: reported, not applied."""
    df = _series("RELIANCE", [1632.6, 1645.4, 818.1, 816.9])
    report = find_unexplained_jumps(df, NO_EVENTS)
    assert len(report) == 1
    row = report.iloc[0]
    assert row["date"] == date(2024, 1, 3)  # the first day at the new price
    assert row["reason"] == "no official action"
    assert row["implied_factor"] == 0.4972  # 818.1 / 1645.4, a hint for the owner


def test_jump_explained_by_an_applied_event_is_not_listed():
    df = _series("RELIANCE", [1632.6, 1645.4, 818.1, 816.9])
    report = find_unexplained_jumps(df, _event("RELIANCE", date(2024, 1, 3)))
    assert report.empty


def test_event_on_another_date_does_not_explain_the_jump():
    df = _series("RELIANCE", [1632.6, 1645.4, 818.1, 816.9])
    report = find_unexplained_jumps(df, _event("RELIANCE", date(2024, 1, 4)))
    assert len(report) == 1


def test_official_review_action_on_that_date_is_named_in_the_reason():
    df = _series("DEMERGER", [100.0, 100.0, 60.0, 61.0])
    review = pd.DataFrame(
        [
            {
                "symbol": "DEMERGER",
                "date": date(2024, 1, 3),
                "action_type": "DEMERGER",
                "subject": "x",
            }
        ]
    )
    report = find_unexplained_jumps(df, NO_EVENTS, review)
    assert report.iloc[0]["reason"] == "official action needs review: DEMERGER"


def test_big_price_rise_is_listed_never_applied():
    """PNB 2017 and YES Bank 2020 rallied about 45 percent: not reverse splits."""
    report = find_unexplained_jumps(_series("PNB", [100.0, 100.0, 146.0, 147.0]), NO_EVENTS)
    assert len(report) == 1
    assert report.iloc[0]["ratio"] < 1  # the price rose


def test_normal_moves_are_ignored_and_circuit_limit_drop_is_listed():
    assert find_unexplained_jumps(_series("DROP10", [100.0, 90.0, 91.0]), NO_EVENTS).empty
    assert len(find_unexplained_jumps(_series("DROP20", [100.0, 80.0, 81.0]), NO_EVENTS)) == 1


def test_missing_previous_trading_day_is_skipped():
    """A price halving after a data gap is not trusted (the gap may hide days of moves)."""
    df = _series("GAPPY", [100.0, 100.0, 50.0])
    all_days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    df = df.drop(index=1)  # symbol has no row on Jan 2, but the market traded that day
    assert find_unexplained_jumps(df, NO_EVENTS, global_trading_days=all_days).empty


def test_market_wide_halving_on_one_date_is_a_data_problem():
    frames = [_series(f"S{i}", [100.0, 100.0, 50.0]) for i in range(10)]
    df = pd.concat(frames, ignore_index=True)
    assert find_unexplained_jumps(df, NO_EVENTS, total_db_symbols=10).empty


def test_report_uses_no_future_prices():
    """No look-ahead: a jump on day t must not change if later prices change."""
    base = _series("NOLOOK", [100.0, 100.0, 50.0, 51.0, 52.0])
    changed = _series("NOLOOK", [100.0, 100.0, 50.0, 500.0, 5.0])
    first = find_unexplained_jumps(base.iloc[:3], NO_EVENTS)
    again = find_unexplained_jumps(changed.iloc[:3], NO_EVENTS)
    pd.testing.assert_frame_equal(first.reset_index(drop=True), again.reset_index(drop=True))
    assert first.iloc[0]["date"] == date(2024, 1, 3)


def test_each_symbol_is_compared_with_its_own_previous_close():
    """Per-symbol shift: a cheap stock after an expensive one must not look like a jump."""
    df = pd.concat(
        [_series("AAA", [1000.0, 1001.0, 1002.0]), _series("BBB", [10.0, 10.1, 10.2])],
        ignore_index=True,
    )
    assert find_unexplained_jumps(df, NO_EVENTS).empty


def test_find_jumps_compares_each_symbol_with_its_own_previous_close():
    """A jump is never measured across two symbols: A's last close and B's first are unrelated."""
    from src.etl.adjust import find_jumps

    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    rows = [("AAA", d, 100.0) for d in days] + [("BBB", d, 10.0) for d in days[:2]]
    rows += [("BBB", days[2], 4.0)]  # a real 2.5x fall inside BBB
    df = pd.DataFrame(rows, columns=["symbol", "date", "close"])

    jumps = find_jumps(df, global_trading_days=days)

    assert jumps[["symbol", "day"]].to_dict("records") == [{"symbol": "BBB", "day": days[2]}]
    assert abs(float(jumps["ratio"].iloc[0]) - 2.5) < 1e-9
