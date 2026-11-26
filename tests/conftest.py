"""Global test configuration and fixtures for nse-screener."""

from __future__ import annotations

import datetime
import sqlite3

# Explicitly register sqlite3 date adapter and converter to address Python 3.12+ deprecation
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_converter("date", lambda b: datetime.date.fromisoformat(b.decode()))


import pytest  # noqa: E402  (kept next to the fixture that needs it)

from tests.real_files_guard import find_changes, snapshot  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _guard_real_files():
    """Fail the whole run if any test changed a real file under data/ or reports/ (task D14).

    Why: redirecting paths one by one (the fixture below) protects only the paths we
    remembered. This guard protects every path, including ones added later: it compares the
    real folders before and after the full run. Do not run a pipeline command while the tests
    run, or its own writes will trip this check.
    """
    from config import settings

    before = snapshot(settings.PROJECT_ROOT)
    yield
    problems = find_changes(before, snapshot(settings.PROJECT_ROOT))
    if problems:
        shown = "\n".join(f"  {line}" for line in problems[:20])
        pytest.fail("Tests touched real files:\n" + shown, pytrace=False)


@pytest.fixture(autouse=True)
def _keep_real_files_untouched(tmp_path_factory, monkeypatch):
    """Point every default output folder at a temporary one, for each test (issue #66, D14).

    Why: a plain ``pytest`` run used to overwrite ``data/processed/indicators.parquet`` with a
    tiny fixture and empty ``reports/*.csv``. The dashboard then found a stale cache and spent
    minutes rebuilding 1.5 million rows on its first page load.

    Code reads these folders from ``config.settings`` when a function runs (not when it is
    defined), so changing the value here is enough. Tests that need their own paths still
    pass them explicitly. The session guard above catches any path that is missed.
    """
    sandbox = tmp_path_factory.mktemp("isolated_real_files")
    monkeypatch.setattr("config.settings.INDICATORS_CACHE_PATH", sandbox / "indicators.parquet")
    monkeypatch.setattr("config.settings.BENCHMARKS_CACHE_PATH", sandbox / "benchmarks.parquet")
    benchmark_cache = sandbox / "benchmark_cache"
    price_cache = sandbox / "bhavcopy_cache"
    monkeypatch.setattr("config.settings.REPORTS_DIR", sandbox / "reports")
    monkeypatch.setattr("config.settings.BENCHMARK_CACHE_DIR", benchmark_cache)
    monkeypatch.setattr(
        "config.settings.BENCHMARK_NON_TRADING_DAYS_FILE", benchmark_cache / "non_trading_days.json"
    )
    monkeypatch.setattr(
        "config.settings.PRICE_NON_TRADING_DAYS_FILE", price_cache / "non_trading_days.json"
    )
    monkeypatch.setattr("config.settings.BHAVCOPY_ALL_DIR", sandbox / "bhavcopy_all")
    monkeypatch.setattr("config.settings.MCAP_DIR", sandbox / "mcap")
    monkeypatch.setattr("config.settings.CORP_ACTIONS_DIR", sandbox / "corporate_actions")


class _NoNetworkSession:
    """Shared NSE session used when a test did not install a fake: any call is a test bug."""

    def __init__(self) -> None:
        self.headers: dict = {}

    def get(self, url, *args, **kwargs):
        raise AssertionError(f"A test tried to reach NSE for real: {url}")


@pytest.fixture(autouse=True)
def nse_sleeps(monkeypatch):
    """Offline NSE client for every test (CL-20): no real network and no real waiting.

    The shared session starts as one that fails loudly. Waits in ``nse_files`` and in the
    corporate-actions downloader are recorded in the returned list instead of slept.
    Install a fake with ``nse_files._session = FakeSession(...)`` (see ``fake_nse_session``).
    """
    from types import SimpleNamespace

    from src.ingestion import nse_files

    nse_files.reset_client()
    nse_files._session = _NoNetworkSession()  # type: ignore[assignment]
    slept: list[float] = []
    fake_time = SimpleNamespace(sleep=slept.append)
    monkeypatch.setattr("src.ingestion.nse_files.time", fake_time)
    monkeypatch.setattr("src.ingestion.corporate_actions.time", fake_time)
    yield slept
    nse_files.reset_client()


@pytest.fixture
def fake_nse_session(monkeypatch):
    """Return a function that installs a fake session as the shared NSE session."""
    from src.ingestion import nse_files

    def install(session):
        monkeypatch.setattr(nse_files, "_session", session)
        return session

    return install


@pytest.fixture
def serve_nse_file(monkeypatch):
    """Return a function that makes ``nse_files.fetch`` answer with the given file content."""

    def serve(content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        monkeypatch.setattr("src.ingestion.nse_files.fetch", lambda url, **kwargs: data)

    return serve
