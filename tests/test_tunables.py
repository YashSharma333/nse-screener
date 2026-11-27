"""CL-31: tunable numbers live in config/settings.py and are read when a function runs."""

from __future__ import annotations

import inspect
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import settings
from scripts.crosscheck_52w import select_crosscheck_dates
from src.db.models import Base
from src.ingestion import nse_files
from src.ingestion.benchmark import load_benchmarks
from src.ingestion.mcap_loader import load_market_caps
from src.ingestion.price_loader import load_raw_prices
from tests.test_load_prices import make_day_udiff_zip


def test_jump_report_settings_replace_the_old_split_names() -> None:
    """The three thresholds only drive the unexplained-jump report, so they say so."""
    assert settings.JUMP_REPORT_MIN_RATIO == 1.25
    assert settings.JUMP_REPORT_MAX_BAD_DAY_SHARE == 0.15
    assert settings.JUMP_REPORT_MIN_SYMBOLS == 5
    assert not [name for name in dir(settings) if name.startswith("SPLIT_")]


def test_price_loader_pause_comes_from_settings_at_call_time(
    nse_sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the setting after import changes the pause between two downloads."""
    monkeypatch.setattr(settings, "PRICE_DOWNLOAD_PAUSE_SECONDS", 0.123)
    day = date(2026, 11, 2)
    monkeypatch.setattr(nse_files, "fetch", lambda url: make_day_udiff_zip(day))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        load_raw_prices(start=day, end=day, session=session, ref_date=date(2026, 11, 20))

    assert nse_sleeps == [0.123]


def test_benchmark_pause_default_means_use_the_setting() -> None:
    """None = read settings when called (a number here would be copied at import time)."""
    assert inspect.signature(load_benchmarks).parameters["delay"].default is None


def test_market_cap_pause_comes_from_settings_at_call_time(
    nse_sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The market cap loader reads the same pause setting when it runs."""
    from decimal import Decimal

    from src.db.models import DailyPrice
    from tests.test_mcap_loader import make_mcap_zip, sample_records

    monkeypatch.setattr(settings, "PRICE_DOWNLOAD_PAUSE_SECONDS", 0.456)
    day = date(2024, 2, 1)
    monkeypatch.setattr(nse_files, "fetch", lambda url: make_mcap_zip(sample_records()))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(DailyPrice(symbol="INFY", date=day, series="EQ", close=Decimal("1")))
        session.flush()
        load_market_caps(start=day, end=day, session=session)

    assert nse_sleeps == [0.456]


def test_crosscheck_history_window_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dates need CROSSCHECK_WINDOW_DAYS of earlier history, whatever the setting says."""
    monkeypatch.setattr(settings, "CROSSCHECK_WINDOW_DAYS", 10)
    first = date(2026, 1, 1)
    dates = [first + timedelta(days=i) for i in range(30)]

    chosen = select_crosscheck_dates(dates, n_dates=100)

    assert min(chosen) == first + timedelta(days=10)


def test_nse_fetch_timeout_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """One timeout for every NSE download (it used to be 30 in one place and 25 in another)."""
    monkeypatch.setattr(settings, "NSE_REQUEST_TIMEOUT_SECONDS", 7)
    seen: list[float] = []

    class FakeResponse:
        status_code = 200
        content = b"PK data"

    class FakeSession:
        def get(self, url, timeout):
            seen.append(timeout)
            return FakeResponse()

    monkeypatch.setattr(nse_files, "_shared_session", lambda: FakeSession())
    nse_files.fetch("https://example.invalid/file.zip")

    assert seen == [7]


def test_crosscheck_tolerance_comes_from_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A difference passes or fails depending on CROSSCHECK_TOLERANCE_PCT at call time."""
    from scripts.crosscheck_52w import print_summary

    report = pd.DataFrame(
        [
            {
                "date": "2024-11-18",
                "symbol": "AAA",
                "metric": "52W_LOW",
                "official": 10.0,
                "ours": 10.3,
                "difference_pct": 3.0,
            }
        ]
    )
    monkeypatch.setattr(settings, "CROSSCHECK_TOLERANCE_PCT", 5.0)
    assert print_summary(report) is True
    monkeypatch.setattr(settings, "CROSSCHECK_TOLERANCE_PCT", 1.0)
    assert print_summary(report) is False
    capsys.readouterr()
