"""Shared helper for the daily-update tests: replace steps with do-nothing fakes."""

from __future__ import annotations

import pytest

from src.etl import daily_update

STEP_FUNCTIONS = (
    "step_load_prices",
    "step_market_cap",
    "step_refresh_reference_data",
    "step_check_bogus_sessions",
    "step_refresh_benchmarks",
    "step_adjust_prices",
    "step_rebuild_indicator_cache",
)


def stub_steps(monkeypatch: pytest.MonkeyPatch, *, keep: tuple[str, ...] = ()) -> None:
    """Make every step a no-op returning {} except those named in ``keep``.

    Tests then run only the real step they care about, with no network or cache work.
    """
    for name in STEP_FUNCTIONS:
        if name not in keep:
            monkeypatch.setattr(daily_update, name, lambda s, ctx: {})
