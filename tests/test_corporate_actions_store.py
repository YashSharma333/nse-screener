"""Tests for corporate-action storage, the adjustment reader and the whole job (task CA1)."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.db.models import Base, CorporateAction, Security, SymbolChange
from src.ingestion.corporate_actions import (
    NSEDownloadError,
    NSEFormatChangedError,
    build_action_rows,
    parse_listing_master,
    parse_symbol_changes,
)
from src.ingestion.corporate_actions_store import (
    get_price_adjustment_events,
    run_corporate_actions_download,
    store_corporate_actions,
    store_symbol_changes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "corporate_actions_sample.json"
TODAY = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 12, 0)

SYMBOL_CHANGE_CSV = (
    b"NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\n"
    b"ICICI Life Insurance Limited,ICICIPRULI,ICICILIFE,23-OCT-2026\n"
    b"Some Other Limited,OLDX,NEWX,01-JAN-2020\n"
)
LISTING_CSV = (
    b"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, "
    b"ISIN NUMBER, FACE VALUE\n"
    b"360ONE,360 ONE WAM LIMITED,EQ,19-SEP-2019,1,1,INE466L01038,1\n"
)


def sample_rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


class FakeDownloader:
    """Stands in for NSEReportsDownloader: returns canned reports, touches no network."""

    def __init__(self, api_rows=None, fail_on_symbol_changes=False):
        # MONNETISPA is a deliberately unreadable real wording; leaving it out keeps the
        # share of unreadable actions under the safety limit, as in the real feed.
        readable = [row for row in sample_rows() if row["symbol"] != "MONNETISPA"]
        self.api_rows = readable if api_rows is None else api_rows
        self.fail_on_symbol_changes = fail_on_symbol_changes

    def fetch_corporate_actions(self, start, end, today=None):
        return self.api_rows

    def fetch_symbol_changes(self):
        if self.fail_on_symbol_changes:
            raise NSEDownloadError("404 for symbolchange.csv")
        return SYMBOL_CHANGE_CSV, parse_symbol_changes(SYMBOL_CHANGE_CSV)

    def fetch_listing_master(self):
        return LISTING_CSV, parse_listing_master(LISTING_CSV)


def count(session: Session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_fund_unit_actions_are_not_stored() -> None:
    """A split of an ETF (ISIN INF...) never reaches the table; company rows are unchanged."""
    etf = {**sample_rows()[0], "symbol": "GOLDBEES", "isin": "INF204KB14I2"}

    without_etf, _ = build_action_rows(sample_rows(), NOW)
    with_etf, _ = build_action_rows([*sample_rows(), etf], NOW)

    assert with_etf == without_etf


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------
def test_storing_twice_changes_nothing(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    store_corporate_actions(session, rows)
    first = count(session, CorporateAction)
    store_corporate_actions(session, rows)
    assert count(session, CorporateAction) == first == len(rows)


def test_storing_again_refreshes_a_changed_row_in_place(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    store_corporate_actions(session, rows)
    changed = [dict(row, company="Renamed Ltd") for row in rows]
    store_corporate_actions(session, changed)
    assert count(session, CorporateAction) == len(rows)
    assert session.execute(select(CorporateAction.company).limit(1)).scalar_one() == "Renamed Ltd"


def test_symbol_changes_are_upserted(session) -> None:
    frame = parse_symbol_changes(SYMBOL_CHANGE_CSV)
    store_symbol_changes(session, frame)
    store_symbol_changes(session, frame)
    assert count(session, SymbolChange) == 2


# ---------------------------------------------------------------------------
# The reader the adjustment code will use
# ---------------------------------------------------------------------------
def test_reader_multiplies_same_day_factors_and_lists_review_items(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    store_corporate_actions(session, rows)
    events, review = get_price_adjustment_events(session)

    def factor(symbol: str, day: date) -> float:
        match = events[(events.symbol == symbol) & (events.date == day)]
        return float(match.factor.iloc[0])

    assert math.isclose(factor("COCHINSHIP", date(2024, 1, 10)), 0.5)
    assert math.isclose(factor("AHCL", date(2026, 4, 24)), 0.1)
    assert math.isclose(factor("HARDWYN", date(2023, 6, 5)), 0.075)
    assert events[events.symbol == "AHCL"].kinds.iloc[0] == "BONUS+SPLIT"
    assert events[events.symbol == "AHCL"].n_parts.iloc[0] == 2
    # Dividends and meetings are never price events.
    assert "PRECWIRE" not in set(events.symbol)
    # Rights, demerger, the NCRPS bonus and the capital-reduction text need a person.
    reviewed = set(review.symbol)
    assert {"IDEA", "AURIONPRO", "RADIOCITY", "MONNETISPA"} <= reviewed
    assert not events.empty
    assert list(events.columns) == ["symbol", "date", "factor", "kinds", "n_parts"]


def test_reader_counts_one_action_once_even_if_listed_under_two_series(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    cochin = next(row for row in rows if row["symbol"] == "COCHINSHIP")
    twin = dict(cochin, series="BE")  # same action, another series
    store_corporate_actions(session, [*rows, twin])
    events, _ = get_price_adjustment_events(session)
    assert math.isclose(float(events[events.symbol == "COCHINSHIP"].factor.iloc[0]), 0.5)


def test_reader_respects_the_date_window(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    store_corporate_actions(session, rows)
    events, review = get_price_adjustment_events(
        session, start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    assert set(events.symbol) == {"COCHINSHIP", "PREMEXPLN"}
    assert review.empty


def test_reader_limits_the_result_to_the_given_symbols(session) -> None:
    rows, _ = build_action_rows(sample_rows(), NOW)
    store_corporate_actions(session, rows)
    events, review = get_price_adjustment_events(session, symbols=["COCHINSHIP"])
    assert set(events.symbol) == {"COCHINSHIP"}
    assert review.empty


def test_reader_on_an_empty_table_returns_empty_frames(session) -> None:
    events, review = get_price_adjustment_events(session)
    assert events.empty
    assert review.empty
    assert list(events.columns) == ["symbol", "date", "factor", "kinds", "n_parts"]


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------
def test_full_job_writes_files_and_tables(session, tmp_path) -> None:
    session.add(Security(symbol="ICICILIFE", name="ICICI Life", status="active"))
    session.commit()

    summary = run_corporate_actions_download(
        start=date(2016, 11, 18),
        end=date(2026, 11, 18),
        downloader=FakeDownloader(),
        session=session,
        output_dir=tmp_path,
        today=TODAY,
    )

    assert summary["api_rows"] == 20
    assert summary["rows_stored"] == count(session, CorporateAction)
    assert summary["by_type"]["BONUS"] == 7
    assert summary["symbol_changes_stored"] == 2
    assert summary["symbol_changes_touching_securities"] == 1  # only ICICILIFE is in securities
    assert summary["listing_rows"] == 1
    assert summary["unparsed_price_share"] < 0.2

    raw = json.loads((tmp_path / "corporate_actions_raw.json").read_text(encoding="utf-8"))
    assert len(raw) == 20  # untouched, duplicates included
    assert (tmp_path / "symbol_changes_raw.csv").read_bytes() == SYMBOL_CHANGE_CSV
    listing = pd.read_parquet(tmp_path / "equity_listing.parquet")
    assert listing.iloc[0]["isin"] == "INE466L01038"
    assert listing.iloc[0]["listing_date"] == date(2019, 9, 19)  # a real date, not text


def test_second_run_adds_no_rows_and_reports_nothing_missing(session, tmp_path) -> None:
    kwargs = {
        "start": date(2016, 11, 18),
        "end": date(2026, 11, 18),
        "downloader": FakeDownloader(),
        "session": session,
        "output_dir": tmp_path,
        "today": TODAY,
    }
    run_corporate_actions_download(**kwargs)
    first = count(session, CorporateAction)
    summary = run_corporate_actions_download(**kwargs)
    assert count(session, CorporateAction) == first
    assert summary["not_seen_in_window"] == 0


def test_rows_nse_no_longer_lists_are_reported_but_kept(session, tmp_path) -> None:
    rows, _ = build_action_rows(FakeDownloader().api_rows, NOW)  # what the fresh feed holds
    store_corporate_actions(session, rows)
    stale = dict(rows[0], subject="Bonus 9:9", subject_hash="f" * 32)  # moved or cancelled
    store_corporate_actions(session, [stale])
    summary = run_corporate_actions_download(
        start=date(2016, 11, 18),
        end=date(2026, 11, 18),
        downloader=FakeDownloader(),
        session=session,
        output_dir=tmp_path,
        today=TODAY,
    )
    assert summary["not_seen_in_window"] == 1
    assert count(session, CorporateAction) == len(rows) + 1  # nothing deleted


def test_unreadable_wording_stops_before_any_database_write(session, tmp_path) -> None:
    # NSE "changes its wording": every split and bonus becomes text we cannot read.
    changed = [
        dict(row, subject="Share issue of one for every two held (bonus)")
        if "onus" in row["subject"] or "plit" in row["subject"]
        else row
        for row in sample_rows()
    ]
    with pytest.raises(NSEFormatChangedError, match="could not be read"):
        run_corporate_actions_download(
            start=date(2016, 11, 18),
            end=date(2026, 11, 18),
            downloader=FakeDownloader(api_rows=changed),
            session=session,
            output_dir=tmp_path,
            today=TODAY,
        )
    assert count(session, CorporateAction) == 0
    assert (tmp_path / "corporate_actions_raw.json").exists()  # raw data is never lost


def test_a_failing_report_leaves_the_database_untouched(session, tmp_path) -> None:
    with pytest.raises(NSEDownloadError):
        run_corporate_actions_download(
            start=date(2016, 11, 18),
            end=date(2026, 11, 18),
            downloader=FakeDownloader(fail_on_symbol_changes=True),
            session=session,
            output_dir=tmp_path,
            today=TODAY,
        )
    assert count(session, CorporateAction) == 0
    assert count(session, SymbolChange) == 0
