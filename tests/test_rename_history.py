"""Tests for task P1a: load the pre-rename price history of renamed stocks.

Cache files are tiny parquet files in tmp_path; the database is SQLite. Real column types
are used (Arrow date32 for TradeDate). Every test describes one rule of the merge:
old rows are ADDED before the new symbol's first day, nothing existing is changed, and only
verified pairs (not mismatch, gap or overlap pairs, not held pairs) are merged.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from config.settings import ALIAS_FALLBACK_MAX_RATIO, SPLIT_REVIEW_MIN_RATIO
from src.db.models import Base, DailyPrice, StockMaster, SymbolAlias, SymbolChange
from src.etl.adjust import rebuild_adjusted
from src.etl.dry_run import dry_run_is_supported
from src.etl.pipeline import BhavcopyETL
from tests.official_actions import add_official_action

DAY0 = date(2024, 1, 1)


def day(n: int) -> date:
    return DAY0 + timedelta(days=n)


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rename.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def write_cache_day(
    cache_dir: Path, when: date, rows: list[dict], with_trade_date: bool = False
) -> None:
    """One cache file named after its date. TradeDate is optional (older files lack it)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(pd.DataFrame(rows))
    if with_trade_date:
        table = table.append_column("TradeDate", pa.array([when] * len(rows), type=pa.date32()))
    pq.write_table(table, cache_dir / f"{when.isoformat()}.parquet")


def bar(symbol: str, close: float, prev_close: float | None = None, volume: int = 1000) -> dict:
    row = {
        "Symbol": symbol,
        "Open": close,
        "High": close,
        "Low": close,
        "Close": close,
        "Volume": volume,
    }
    if prev_close is not None:
        row["PrevClose"] = prev_close
    return row


def build_pair(
    cache_dir: Path,
    old: str = "OLDCO",
    new: str = "NEWCO",
    old_days: int = 3,
    new_days: int = 3,
    old_close: float = 100.0,
    new_first_close: float = 100.0,
    with_prev_close: bool = True,
) -> None:
    """Cache: old symbol on days 0..old_days-1, then the new symbol for new_days days."""
    for n in range(old_days):
        write_cache_day(cache_dir, day(n), [bar(old, old_close, old_close if n else None)])
    for n in range(new_days):
        close = new_first_close if n == 0 else new_first_close + n
        prev = (old_close if n == 0 else new_first_close + n - 1) if with_prev_close else None
        write_cache_day(cache_dir, day(old_days + n), [bar(new, close, prev)])


def seed_new_symbol(session: Session, new: str, first_day: int, count: int, close: float) -> None:
    """The database only holds the new symbol from its rename day (the real #60 situation)."""
    session.add(
        StockMaster(
            symbol=new,
            company_name=new,
            cap_bucket="Unknown",
            in_universe=True,
            status="active",
            added_on=day(0),
        )
    )
    for n in range(count):
        session.add(
            DailyPrice(
                symbol=new,
                date=day(first_day + n),
                open=999.0,
                high=999.0,
                low=999.0,
                close=999.0,
                volume=5,
                index_name="largecap",
            )
        )
    session.add(SymbolAlias(old_symbol="OLDCO", new_symbol=new, status="candidate"))
    session.commit()
    assert close  # (close kept for readable call sites)


def run_apply(etl: BhavcopyETL, session: Session, tmp_path: Path, dry_run: bool, **kw):
    with (
        patch.object(etl, "rebuild_adjusted_prices"),
        patch("src.db.data_service.load_indicator_snapshot"),
    ):
        return etl.apply_symbol_aliases(
            dry_run=dry_run, session=session, reports_dir=tmp_path / "reports", **kw
        )


def count_rows(session: Session, symbol: str) -> int:
    return session.scalar(
        select(func.count()).select_from(DailyPrice).where(DailyPrice.symbol == symbol)
    )


# ---------------------------------------------------------------------------
# The merge itself
# ---------------------------------------------------------------------------
def test_verified_pair_adds_old_rows_before_new_first_day_and_changes_nothing_else(
    tmp_path, session_factory
):
    cache = tmp_path / "cache"
    build_pair(cache)  # old days 0-2, new days 3-5
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", first_day=3, count=3, close=100.0)
        etl = BhavcopyETL(cache_dir=cache)

        result = run_apply(etl, s, tmp_path, dry_run=False)

        assert result["summaries"][0]["rows_added"] == 3
        rows = s.scalars(select(DailyPrice).where(DailyPrice.symbol == "NEWCO")).all()
        assert sorted(r.date for r in rows) == [day(n) for n in range(6)]
        # Existing rows keep every value; the added rows carry the cache values.
        existing = [r for r in rows if r.date >= day(3)]
        assert all(float(r.close) == 999.0 and r.volume == 5 for r in existing)
        added = [r for r in rows if r.date < day(3)]
        assert all(float(r.close) == 100.0 for r in added)
        # index_name: copied from the new symbol's own rows, never NULL.
        assert all(r.index_name == "largecap" for r in rows)
        assert (tmp_path / "reports" / "alias_added_OLDCO_to_NEWCO.csv").exists()
        assert len(pd.read_csv(tmp_path / "reports" / "alias_added_OLDCO_to_NEWCO.csv")) == 3
        assert s.get(SymbolAlias, ("OLDCO", "NEWCO")).status == "applied"


def test_second_run_adds_nothing(tmp_path, session_factory):
    cache = tmp_path / "cache"
    build_pair(cache)
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 3, 3, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        run_apply(etl, s, tmp_path, dry_run=False)
        second = run_apply(etl, s, tmp_path, dry_run=False)
        assert second["summaries"][0]["rows_added"] == 0
        assert count_rows(s, "NEWCO") == 6


def test_dry_run_writes_nothing_and_files_no_issue(tmp_path, session_factory):
    cache = tmp_path / "cache"
    build_pair(cache)
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 3, 3, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        with patch("src.etl.pipeline.create_github_issue") as issue:
            result = run_apply(etl, s, tmp_path, dry_run=True)
        assert result["summaries"][0]["rows_added"] == 3  # reports what WOULD be added
        assert count_rows(s, "NEWCO") == 3
        assert s.get(SymbolAlias, ("OLDCO", "NEWCO")).status == "candidate"
        assert not (tmp_path / "reports" / "alias_added_OLDCO_to_NEWCO.csv").exists()
        issue.assert_not_called()


@pytest.mark.parametrize("case", ["ratio_mismatch", "date_gap", "overlap"])
def test_unverified_pairs_are_not_merged(tmp_path, session_factory, case):
    cache = tmp_path / "cache"
    if case == "ratio_mismatch":  # a 2:1 split on the rename day: PrevClose says 100 -> 50
        build_pair(cache)
        write_cache_day(cache, day(3), [bar("NEWCO", 50.0, 50.0)])
    elif case == "date_gap":  # old symbol misses the last day before the new one starts
        build_pair(cache)
        (cache / f"{day(2).isoformat()}.parquet").unlink()
        write_cache_day(cache, day(2), [bar("OTHER", 10.0, 10.0)])
    else:  # both symbols trade on the same day at different prices
        build_pair(cache)
        write_cache_day(cache, day(3), [bar("OLDCO", 80.0, 100.0), bar("NEWCO", 100.0, 100.0)])
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 3, 3, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        with patch("src.etl.pipeline.create_github_issue"):
            result = run_apply(etl, s, tmp_path, dry_run=False)
        assert result["summaries"][0]["classification"] == case
        assert result["summaries"][0]["status"] == "rejected"
        assert count_rows(s, "NEWCO") == 3


def test_held_pair_is_never_applied(tmp_path, session_factory):
    cache = tmp_path / "cache"
    build_pair(cache)
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 3, 3, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        result = run_apply(etl, s, tmp_path, dry_run=False, holds={("OLDCO", "NEWCO")})
        assert result["summaries"][0]["status"] == "held"
        assert count_rows(s, "NEWCO") == 3


def test_load_alias_holds_reads_the_owner_file(tmp_path):
    path = tmp_path / "holds.csv"
    path.write_text("# comment\nold_symbol,new_symbol,reason\npel,piramalfin,why\n")
    assert BhavcopyETL.load_alias_holds(path) == {("PEL", "PIRAMALFIN")}
    assert BhavcopyETL.load_alias_holds(tmp_path / "missing.csv") == set()


# ---------------------------------------------------------------------------
# Continuity without PrevClose (cache files from 2023 onward)
# ---------------------------------------------------------------------------
def test_fallback_classifies_a_pair_without_prev_close(tmp_path):
    cache = tmp_path / "cache"
    # New first close 104 versus old close 100: a normal day, no PrevClose, no TradeDate.
    build_pair(cache, new_first_close=104.0, with_prev_close=False)
    result = BhavcopyETL(cache_dir=cache).check_alias_continuity("OLDCO", "NEWCO")
    assert result["classification"] == "verified_close"
    assert result["used_fallback"] is True
    assert result["new_prev_close"] is None


def test_fallback_rejects_a_split_sized_move(tmp_path):
    cache = tmp_path / "cache"
    build_pair(cache, new_first_close=40.0, with_prev_close=False)
    result = BhavcopyETL(cache_dir=cache).check_alias_continuity("OLDCO", "NEWCO")
    assert result["classification"] == "ratio_mismatch"
    assert result["used_fallback"] is True


def test_fallback_band_is_the_split_review_ratio():
    assert ALIAS_FALLBACK_MAX_RATIO == SPLIT_REVIEW_MIN_RATIO


def test_cache_file_with_trade_date_and_without_both_load(tmp_path):
    cache = tmp_path / "cache"
    write_cache_day(cache, day(0), [bar("OLDCO", 100.0, 99.0)], with_trade_date=True)
    write_cache_day(cache, day(1), [bar("NEWCO", 100.0, 100.0)])  # no TradeDate column
    result = BhavcopyETL(cache_dir=cache).check_alias_continuity("OLDCO", "NEWCO")
    assert result["classification"] == "verified_exact"


# ---------------------------------------------------------------------------
# Renamed symbols stay renamed
# ---------------------------------------------------------------------------
def test_old_symbol_is_treated_as_renamed_after_apply(tmp_path, session_factory):
    cache = tmp_path / "cache"
    build_pair(cache)
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 3, 3, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        run_apply(etl, s, tmp_path, dry_run=False)
        # Ingestion and --fill-missing-symbols use this set to skip the old name.
        assert "OLDCO" in etl._get_renamed_symbols(session=s)
        assert count_rows(s, "OLDCO") == 0


# ---------------------------------------------------------------------------
# Adjustment: a split BEFORE the rename day (filed under the new symbol)
# ---------------------------------------------------------------------------
def test_split_before_rename_day_is_applied_to_merged_history(tmp_path, session_factory):
    cache = tmp_path / "cache"
    # Old symbol trades at 200 on days 0-1, splits 2:1 on day 2 (price 100), renamed on day 4.
    for n, close in enumerate([200.0, 200.0, 100.0, 100.0]):
        write_cache_day(cache, day(n), [bar("OLDCO", close, 200.0 if n < 3 else 100.0)])
    for n in range(2):
        write_cache_day(cache, day(4 + n), [bar("NEWCO", 100.0, 100.0)])
    with session_factory() as s:
        seed_new_symbol(s, "NEWCO", 4, 2, 100.0)
        etl = BhavcopyETL(cache_dir=cache)
        run_apply(etl, s, tmp_path, dry_run=False)
        # NSE files the split under the CURRENT symbol, with an ex-date inside the merged history.
        add_official_action(s, "NEWCO", day(2), factor=0.5)
        s.commit()
        rebuild_adjusted(session=s)
        rows = s.scalars(
            select(DailyPrice).where(DailyPrice.symbol == "NEWCO").order_by(DailyPrice.date)
        ).all()
        adj = [float(r.adj_close) for r in rows if r.date <= day(3)]
        assert adj == [100.0, 100.0, 100.0, 100.0]  # continuous across the ex-date


# ---------------------------------------------------------------------------
# symbol_changes as candidate evidence
# ---------------------------------------------------------------------------
def test_symbol_changes_become_candidates_with_dry_run_default(session_factory):
    with session_factory() as s:
        s.add_all(
            [
                DailyPrice(symbol="NEWA", date=day(5), close=1.0),
                DailyPrice(symbol="NEWB", date=day(5), close=1.0),
                DailyPrice(symbol="NEWC", date=day(5), close=1.0),
                DailyPrice(symbol="HASPRICES", date=day(0), close=1.0),
                DailyPrice(symbol="NEWD", date=day(5), close=1.0),
                SymbolChange(old_symbol="OLDA", new_symbol="NEWA", effective_date=day(5)),
                # one old symbol used for two companies -> reused
                SymbolChange(old_symbol="DUP", new_symbol="NEWB", effective_date=day(5)),
                SymbolChange(old_symbol="DUP", new_symbol="NEWC", effective_date=day(6)),
                # chain: NEWD is itself an old symbol
                SymbolChange(old_symbol="OLDD", new_symbol="NEWD", effective_date=day(5)),
                SymbolChange(old_symbol="NEWD", new_symbol="NEWE", effective_date=day(9)),
                # old symbol still has prices -> not a missing history
                SymbolChange(old_symbol="HASPRICES", new_symbol="NEWA", effective_date=day(5)),
                # before our first price date -> outside the history
                SymbolChange(old_symbol="ANCIENT", new_symbol="NEWA", effective_date=day(-900)),
                # new symbol has no prices
                SymbolChange(old_symbol="OLDX", new_symbol="NOPRICES", effective_date=day(5)),
            ]
        )
        s.commit()
        etl = BhavcopyETL()

        dry = etl.import_symbol_change_aliases(dry_run=True, session=s)
        assert [r["old_symbol"] for r in dry["records"]] == ["OLDA"]
        assert sorted(old for old, _ in dry["skipped"]["reused"]) == ["DUP", "DUP"]
        assert dry["skipped"]["chain"] == [("OLDD", "NEWD")]
        assert s.scalar(select(func.count()).select_from(SymbolAlias)) == 0  # dry run

        etl.import_symbol_change_aliases(dry_run=False, session=s)
        alias = s.get(SymbolAlias, ("OLDA", "NEWA"))
        assert alias.status == "candidate"
        # Re-running reports it as known and never overwrites it.
        alias.status = "verified_exact"
        s.commit()
        again = etl.import_symbol_change_aliases(dry_run=False, session=s)
        assert again["candidates"] == 0 and again["skipped"]["known"] == [("OLDA", "NEWA")]
        assert s.get(SymbolAlias, ("OLDA", "NEWA")).status == "verified_exact"


def test_dry_run_flag_is_accepted_for_symbol_change_import():
    import argparse

    args = argparse.Namespace(dry_run=True, import_symbol_change_aliases=True)
    assert dry_run_is_supported(args)
