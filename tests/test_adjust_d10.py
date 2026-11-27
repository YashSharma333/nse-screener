"""
Unit tests for Task D10: Corporate actions on shared fetch, price adjustments & gap reporting.
=============================================================================================
Tests run offline without network or MySQL access.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scripts.crosscheck_52w import print_summary, run_crosscheck_for_date
from src.db.models import AdjFactor, Base, DailyPrice, SymbolChange
from src.etl.adjust import (
    CorporateActionsMissingError,
    find_big_gaps,
    find_unexplained_jumps,
    read_official_events,
    rebuild_adjusted,
)
from src.ingestion import nse_files
from tests.official_actions import add_official_action, add_prices


@pytest.fixture
def session():
    """Isolated in-memory SQLite database."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_events_filtered_to_active_symbols(session: Session) -> None:
    """An event for a symbol that has no price history produces no factor in adj_factors."""
    # Stored prices exist only for RELIANCE
    add_prices(session, "RELIANCE", [100.0, 100.0, 50.0], start=date(2024, 1, 1))
    add_official_action(session, "RELIANCE", date(2024, 1, 3), factor=0.5)

    # Inactive/delisted symbol with no stored prices
    add_official_action(session, "DELISTED_XYZ", date(2024, 1, 2), factor=0.5)

    rebuild_adjusted(session=session)

    # Only RELIANCE should have an entry in adj_factors
    factors = session.scalars(select(AdjFactor)).all()
    assert len(factors) == 1
    assert factors[0].symbol == "RELIANCE"
    assert factors[0].factor == 0.5


def test_event_under_former_name_applies_to_renamed_stock(session: Session) -> None:
    """An event filed under a former name (OLD) applies backwards across the renamed stock."""
    # Register rename in symbol_changes: OLD -> NEW on 2024-03-01
    session.add(
        SymbolChange(
            old_symbol="OLD",
            new_symbol="NEW",
            effective_date=date(2024, 3, 1),
            company="Test Company",
        )
    )
    session.flush()

    # Prices under OLD before rename: 2024-01-01 to 2024-01-03
    add_prices(session, "OLD", [200.0, 200.0, 100.0], start=date(2024, 1, 1))
    # Prices under NEW after rename: 2024-03-01 to 2024-03-03
    add_prices(session, "NEW", [100.0, 100.0, 100.0], start=date(2024, 3, 1))

    # Corporate action filed under OLD on 2024-01-03 (split 2:1 -> factor 0.5)
    add_official_action(session, "OLD", date(2024, 1, 3), factor=0.5)

    rebuild_adjusted(session=session)

    # Check adjusted close for OLD rows
    old_rows = session.scalars(
        select(DailyPrice).where(DailyPrice.symbol == "OLD").order_by(DailyPrice.date)
    ).all()
    assert len(old_rows) == 3
    # Rows before 2024-01-03 were 200.0 -> adjusted by 0.5 to 100.0
    assert float(old_rows[0].adj_close) == 100.0
    assert float(old_rows[1].adj_close) == 100.0
    # Row on 2024-01-03 was 100.0 -> unadjusted (100.0)
    assert float(old_rows[2].adj_close) == 100.0


def test_split_factor_exact(session: Session) -> None:
    """10:2 split (factor 0.2) adjusts prices exactly without rounding distortion."""
    days = [date(2024, 5, 1) + timedelta(days=i) for i in range(3)]
    # Closes: 500, 500, 100 (split on day 3)
    for d, c in zip(days, [500.0, 500.0, 100.0], strict=True):
        session.add(
            DailyPrice(symbol="SPLITCO", date=d, open=c, high=c, low=c, close=c, volume=1000)
        )
    session.flush()

    add_official_action(session, "SPLITCO", days[2], factor=0.2)

    rebuild_adjusted(session=session)

    rows = session.scalars(
        select(DailyPrice).where(DailyPrice.symbol == "SPLITCO").order_by(DailyPrice.date)
    ).all()
    assert float(rows[0].adj_close) == 100.0
    assert float(rows[1].adj_close) == 100.0
    assert float(rows[2].adj_close) == 100.0


def test_raw_close_unchanged_after_rebuild(session: Session) -> None:
    """Raw OHLCV columns in daily_prices are strictly identical before and after rebuild."""
    add_prices(session, "SAFECO", [120.0, 125.0, 60.0], start=date(2024, 2, 1))
    add_official_action(session, "SAFECO", date(2024, 2, 3), factor=0.5)

    before = [
        (r.open, r.high, r.low, r.close, r.volume)
        for r in session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    ]

    rebuild_adjusted(session=session)

    after = [
        (r.open, r.high, r.low, r.close, r.volume)
        for r in session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    ]

    assert before == after
    # But adj_close changed
    adj_closes = [
        float(r.adj_close)
        for r in session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    ]
    assert adj_closes == [60.0, 62.5, 60.0]


def test_override_apply_and_ignore_still_win(session: Session, tmp_path: Path) -> None:
    """Owner overrides in adjustment_overrides.csv take final precedence."""
    add_prices(session, "OV_TEST", [100.0, 100.0, 50.0], start=date(2024, 1, 1))
    add_official_action(session, "OV_TEST", date(2024, 1, 3), factor=0.5)

    # 1. 'ignore' override cancels the official event
    overrides_file = tmp_path / "adjustment_overrides.csv"
    overrides_file.write_text(
        "symbol,date,action,factor,reason\nOV_TEST,2024-01-03,ignore,,owner canceled\n",
        encoding="utf-8",
    )
    rebuild_adjusted(session=session, overrides_path=overrides_file)
    session.expire_all()

    rows = session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    # Unadjusted because ignored
    assert float(rows[0].adj_close) == 100.0

    # 2. 'apply' override forces a specific factor (e.g. 0.2 instead of 0.5)
    overrides_file.write_text(
        "symbol,date,action,factor,reason\nOV_TEST,2024-01-03,apply,0.2,owner custom factor\n",
        encoding="utf-8",
    )
    rebuild_adjusted(session=session, overrides_path=overrides_file)
    session.expire_all()
    rows = session.scalars(select(DailyPrice).order_by(DailyPrice.date)).all()
    assert float(rows[0].adj_close) == 20.0


def test_crosscheck_flags_difference_over_tolerance_and_does_not_modify_prices(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """52-week cross-check flags discrepancies and leaves database prices completely untouched."""
    check_day = date(2024, 11, 18)
    # Add prices for CHECK_SYM in DB
    add_prices(session, "CHECK_SYM", [100.0, 150.0, 120.0], start=date(2024, 11, 1))

    # Fake official 52-week CSV with official high = 200.0 (ours is 150.0 -> difference 25%)
    fake_csv = (
        b'"Disclaimer - The Data..."\n'
        b'"Effective for 18-Nov-2024"\n'
        b'"SYMBOL","SERIES","Adjusted 52_Week_High","52_Week_High_Date",'
        b'"Adjusted 52_Week_Low","52_Week_Low_DT"\n'
        b'"CHECK_SYM","EQ","200.00","01-AUG-2024","100.00","17-NOV-2023"\n'
    )

    monkeypatch.setattr(nse_files, "fetch", lambda _url: fake_csv)

    # Record DB state before crosscheck
    before_prices = [
        (r.open, r.high, r.low, r.close, r.adj_close)
        for r in session.scalars(select(DailyPrice)).all()
    ]

    recs = run_crosscheck_for_date(check_day, session)
    assert len(recs) == 2  # 52W_HIGH and 52W_LOW

    high_rec = next(r for r in recs if r["metric"] == "52W_HIGH")
    assert high_rec["symbol"] == "CHECK_SYM"
    assert high_rec["official"] == 200.0
    assert high_rec["ours"] == 150.0
    assert high_rec["difference_pct"] == 25.0

    # Verify DB prices were not touched at all
    after_prices = [
        (r.open, r.high, r.low, r.close, r.adj_close)
        for r in session.scalars(select(DailyPrice)).all()
    ]
    assert before_prices == after_prices


def test_empty_corporate_actions_table_stops_with_clear_error(session: Session) -> None:
    """read_official_events stops with error if corporate_actions table is empty."""
    with pytest.raises(CorporateActionsMissingError, match="corporate_actions table is empty"):
        read_official_events(session)


def test_unexplained_jumps_original_behavior() -> None:
    """find_unexplained_jumps identifies overnight price drops >= 20% without explanation."""
    prices_df = pd.DataFrame(
        {
            "symbol": ["TMPV", "TMPV"],
            "date": [date(2025, 10, 24), date(2025, 10, 27)],
            "close": [400.0, 250.0],  # 37.5% drop overnight (ratio = 1.6)
        }
    )
    events_df = pd.DataFrame(columns=["symbol", "date", "factor", "ratio_kind", "source", "kinds"])
    official_review = pd.DataFrame(
        [
            {
                "symbol": "TMPV",
                "date": date(2025, 10, 27),
                "action_type": "DEMERGER",
                "subject": "DEMERGER",
            }
        ]
    )

    jumps = find_unexplained_jumps(prices_df, events_df, official_review=official_review)
    assert len(jumps) == 1
    assert jumps.iloc[0]["symbol"] == "TMPV"
    assert jumps.iloc[0]["ratio"] == 1.6
    assert "DEMERGER" in jumps.iloc[0]["reason"]


def test_big_gaps_classification() -> None:
    """find_big_gaps tags GAP_DOWN for >= 20% drops and GAP_UP for >= 25% surges."""
    prices_df = pd.DataFrame(
        {
            "symbol": ["STOCK_A", "STOCK_A", "STOCK_B", "STOCK_B"],
            "date": [
                date(2024, 5, 2),
                date(2024, 5, 3),
                date(2024, 5, 2),
                date(2024, 5, 3),
            ],
            "close": [
                100.0,
                75.0,  # Drop of 25% -> GAP_DOWN
                100.0,
                130.0,  # Surge of 30% -> GAP_UP
            ],
        }
    )

    gaps = find_big_gaps(prices_df)
    assert len(gaps) == 2

    down = gaps[gaps["symbol"] == "STOCK_A"].iloc[0]
    assert down["move_type"] == "GAP_DOWN"
    assert down["pct_change"] == -25.0

    up = gaps[gaps["symbol"] == "STOCK_B"].iloc[0]
    assert up["move_type"] == "GAP_UP"
    assert up["pct_change"] == 30.0


def test_crosscheck_summary_states_the_definition_and_passes_or_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed summary names the 365-day definition; above the tolerance it says FAIL."""
    close = pd.DataFrame(
        [
            {
                "date": "2024-11-18",
                "symbol": "AAA",
                "metric": "52W_HIGH",
                "official": 100.0,
                "ours": 100.5,
                "difference_pct": 0.5,
            }
        ]
    )
    far = close.assign(difference_pct=25.0)

    assert print_summary(close) is True
    first = capsys.readouterr().out
    assert "365 calendar days" in first and "not the 252-bar cache column" in first
    assert "Result: PASS" in first

    assert print_summary(far) is False
    assert "Result: FAIL" in capsys.readouterr().out
    assert print_summary(close.iloc[0:0]) is False  # nothing compared is not a pass
