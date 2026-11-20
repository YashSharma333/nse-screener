"""Tests for the UI "Sync Market Data" helper (full daily update, not the Nifty-500 filter)."""

from __future__ import annotations

from datetime import date

import pytest

from src.etl import daily_update
from src.etl.daily_update import DailyUpdateError, DailyUpdateSummary
from ui.data import run_full_update


def test_sync_runs_full_daily_update_and_reports_new_days(monkeypatch):
    """Regression: the button must run run_daily_update, never BhavcopyETL.run_incremental."""
    summary = DailyUpdateSummary(new_dates=[date(2026, 11, 20)], rows_added=3400)
    monkeypatch.setattr(daily_update, "run_daily_update", lambda: summary)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("UI sync must not use the Nifty-500 incremental ETL")

    monkeypatch.setattr("src.etl.pipeline.BhavcopyETL.run_incremental", _forbidden)

    result = run_full_update()
    assert result["status"] == "success"
    assert "3,400" in result["message"]


def test_sync_up_to_date(monkeypatch):
    monkeypatch.setattr(daily_update, "run_daily_update", lambda: DailyUpdateSummary())
    assert run_full_update()["status"] == "up_to_date"


def test_sync_shows_failed_step(monkeypatch):
    def _boom():
        raise DailyUpdateError(2, "market_cap", "network down")

    monkeypatch.setattr(daily_update, "run_daily_update", _boom)
    result = run_full_update()
    assert result["status"] == "error"
    assert "Step 2" in result["message"]
    assert "market_cap" in result["message"]


def test_sync_does_not_hide_unexpected_errors(monkeypatch):
    """Only known failure types are turned into a message; real bugs still surface."""

    def _bug():
        raise KeyError("bug")

    monkeypatch.setattr(daily_update, "run_daily_update", _bug)
    with pytest.raises(KeyError):
        run_full_update()
