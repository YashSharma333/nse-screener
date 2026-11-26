import unittest

from src.db.models import DailyPrice


class TestModels(unittest.TestCase):
    def test_daily_price_model_index_name(self):
        # In task D4, index_name was dropped from daily_prices; series, market_cap, isin were added
        self.assertFalse(hasattr(DailyPrice, "index_name"))
        self.assertTrue(hasattr(DailyPrice, "series"))
        self.assertTrue(hasattr(DailyPrice, "market_cap"))
        self.assertTrue(hasattr(DailyPrice, "isin"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# CL-31: simple CLI parser, one date pair, no logging on import
# ---------------------------------------------------------------------------
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402

import run_pipeline  # noqa: E402
from config.settings import PROJECT_ROOT  # noqa: E402

ACTION_FLAGS = [
    "--update",
    "--load-prices",
    "--load-mcap",
    "--download-corporate-actions",
    "--rebuild-adjusted",
    "--benchmarks",
    "--refresh-cache",
    "--verify-prices",
    "--crosscheck-52w",
]


def test_two_action_flags_are_refused_with_an_argparse_error(capsys) -> None:
    """--update --verify-prices used to run only the first flag and ignore the second."""
    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(["--update", "--verify-prices"])

    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--load-securities", "--load-renames", "--start", "--end"])
def test_removed_flags_are_refused(flag: str, capsys) -> None:
    """The flags removed in CL-31 (LOG-019) no longer exist; --update covers the first two."""
    argv = [flag] if flag.startswith("--load") else ["--benchmarks", flag, "2026-01-01"]
    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(argv)

    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_invalid_date_gives_a_clean_error_without_traceback(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(["--load-prices", "--from", "2026-13-45"])

    err = capsys.readouterr().err
    assert exc_info.value.code == 2
    assert "invalid date" in err and "YYYY-MM-DD" in err
    assert "Traceback" not in err


def test_date_flags_are_refused_for_actions_that_ignore_them(capsys) -> None:
    """--update has no date range, so --from must not be silently ignored."""
    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(["--update", "--from", "2026-01-01"])

    assert exc_info.value.code == 2
    assert "--from/--to" in capsys.readouterr().err


def test_from_after_to_is_refused(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(["--benchmarks", "--from", "2026-02-01", "--to", "2026-01-01"])

    assert exc_info.value.code == 2
    assert "after" in capsys.readouterr().err


def test_benchmarks_uses_from_and_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """--benchmarks used to ignore --from/--to; now they are the range."""
    seen: list[tuple[date, date]] = []
    monkeypatch.setattr(run_pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(run_pipeline, "refresh_indicator_cache", lambda: None)
    monkeypatch.setattr(
        "src.ingestion.benchmark.load_benchmark_range",
        lambda start, end: seen.append((start, end)) or 0,
    )

    run_pipeline.main(["--benchmarks", "--from", "2026-01-01", "--to", "2026-01-31"])

    assert seen == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_every_action_flag_is_in_the_parser_and_none_is_required() -> None:
    parser = run_pipeline.build_parser()
    assert parser.parse_args([]).update is False
    for flag in ACTION_FLAGS:
        assert getattr(parser.parse_args([flag]), flag[2:].replace("-", "_")) is True


def test_importing_database_modules_sets_up_no_logging() -> None:
    """Importing src.db.session / data_service must not add log handlers (or create log files)."""
    code = (
        "import logging, src.db.session, src.db.data_service;"
        "print(len(logging.getLogger().handlers))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    )
    assert proc.stdout.strip() == "0"
