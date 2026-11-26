#!/usr/bin/env python3
"""
CLI runner for the NSE data pipeline.

Every data operation is one flag. For the daily routine just run:

    python run_pipeline.py --update

Run it with --help to see all commands. Only one command can be given at a time.
"""

# Every run_*_command takes (args) so main() can call them all the same way; some ignore it.
# ruff: noqa: ARG001

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import date

from config.settings import NSE_HISTORY_START, setup_logging

logger = logging.getLogger("run_pipeline")


def parse_date(text: str) -> date:
    """Turn YYYY-MM-DD into a date; a bad value becomes a clean argparse error (no traceback)."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {text!r}, expected YYYY-MM-DD") from None


def run_corporate_actions_command(args: argparse.Namespace) -> None:
    """Run --download-corporate-actions and print a short summary for the owner."""
    from src.ingestion.corporate_actions import NSEDownloadError, NSEFormatChangedError
    from src.ingestion.corporate_actions_store import run_corporate_actions_download

    try:
        summary = run_corporate_actions_download(start=args.from_date, end=args.to_date)
    except (NSEDownloadError, NSEFormatChangedError) as exc:
        # These are expected failures with a clear message, so no traceback.
        logger.error("Corporate actions download stopped: %s", exc)
        sys.exit(1)

    logger.info(
        "Corporate actions: %d API rows -> %d stored (%d exact-factor events, %d to review).",
        summary["api_rows"],
        summary["rows_stored"],
        summary["exact_factor_events"],
        summary["needs_review"],
    )
    logger.info(
        "Symbol changes stored: %d (%d touch our securities list). Listing master rows: %d.",
        summary["symbol_changes_stored"],
        summary["symbol_changes_touching_securities"],
        summary["listing_rows"],
    )


def refresh_indicator_cache() -> None:
    """Rebuild the indicator Parquet cache from MySQL (no downloads)."""
    from src.db.data_service import load_indicator_snapshot

    snapshot, _full, is_live, msg = load_indicator_snapshot(force_refresh=True)
    logger.info("Indicator cache rebuilt: %d symbols, live=%s (%s)", len(snapshot), is_live, msg)


def run_daily_update_command(args: argparse.Namespace) -> None:
    """Run --update. A failed step is reported in one clear line and exits non-zero."""
    from src.etl.daily_update import DailyUpdateError, run_daily_update

    try:
        run_daily_update()
    except DailyUpdateError as exc:
        logger.error("%s", exc)
        sys.exit(1)


def run_load_prices_command(args: argparse.Namespace) -> None:
    from src.ingestion.price_loader import load_raw_prices

    load_raw_prices(start=args.from_date, end=args.to_date)


def run_load_mcap_command(args: argparse.Namespace) -> None:
    from src.ingestion.mcap_loader import load_market_caps

    load_market_caps(start=args.from_date, end=args.to_date)


def run_benchmarks_command(args: argparse.Namespace) -> None:
    """Run --benchmarks: load index closes for the range, then refresh the cache."""
    from src.ingestion.benchmark import load_benchmark_range

    start_date = args.from_date or NSE_HISTORY_START
    end_date = args.to_date or date.today()
    saved = load_benchmark_range(start_date, end_date)
    logger.info("Benchmark ingestion complete. Ingested: %d records.", saved)
    refresh_indicator_cache()


def run_rebuild_adjusted_command(args: argparse.Namespace) -> None:
    """Run --rebuild-adjusted: recompute adjusted prices, then refresh the cache."""
    from src.etl.adjust import rebuild_adjusted

    updated = rebuild_adjusted()
    logger.info("Adjusted prices rebuild complete. Updated: %d rows.", updated)
    refresh_indicator_cache()


def run_refresh_cache_command(args: argparse.Namespace) -> None:
    refresh_indicator_cache()


def run_verify_prices_command(args: argparse.Namespace) -> None:
    from scripts.verify_prices import verify_prices

    diff_count, _ = verify_prices()
    if diff_count > 0:
        logger.error("Price verification failed with %d discrepancies.", diff_count)
        sys.exit(1)
    logger.info("Price verification succeeded with 0 discrepancies.")


def run_crosscheck_command(args: argparse.Namespace) -> None:
    from scripts.crosscheck_52w import crosscheck_52w

    logger.info("52-week cross-check completed with %d comparisons.", len(crosscheck_52w()))


# One row per command: (flag, function that runs it, does it use --from/--to?, help text).
COMMANDS: list[tuple[str, Callable[[argparse.Namespace], None], bool, str]] = [
    (
        "--update",
        run_daily_update_command,
        False,
        "The daily routine: load new prices up to today, market cap, securities, symbol "
        "changes and corporate actions, check new dates for bogus sessions (report only), "
        "refresh benchmarks, adjust prices and rebuild the indicator cache. Stops at the "
        "first failing step.",
    ),
    (
        "--load-prices",
        run_load_prices_command,
        True,
        "Ingest official raw Bhavcopy prices (all regular trading series: EQ, BE, BZ, SM, ST) "
        "from NSE archives into daily_prices. Resumable, polite, and verified. Default range: "
        "2016-11-21 to today.",
    ),
    (
        "--load-mcap",
        run_load_mcap_command,
        True,
        "Fill daily_prices.market_cap from official NSE PR bundles (PRDDMMYY.zip) "
        "for trading days from 2024-02-01 to today (or --from / --to).",
    ),
    (
        "--download-corporate-actions",
        run_corporate_actions_command,
        True,
        "Download NSE's official corporate actions (splits, bonuses ...), symbol changes "
        "and listing master, keep the raw files in data/corporate_actions/ and upsert "
        "the parsed rows into MySQL. Default window: 10 years back to 90 days ahead; "
        "use --from / --to to change it.",
    ),
    (
        "--rebuild-adjusted",
        run_rebuild_adjusted_command,
        False,
        "Recompute corporate action price adjustments, update the database and refresh "
        "the indicator cache. Run it after every corporate-actions download.",
    ),
    (
        "--benchmarks",
        run_benchmarks_command,
        True,
        "Download benchmark index closes for --from to --to (default: 2016-11-21 to "
        "today) and refresh the indicator cache. The daily --update already does this "
        "for new days.",
    ),
    (
        "--refresh-cache",
        run_refresh_cache_command,
        False,
        "Rebuild the pre-computed indicator Parquet cache from MySQL without downloading",
    ),
    (
        "--verify-prices",
        run_verify_prices_command,
        False,
        "Independent accuracy check: re-downloads ~48 trading dates fresh from NSE, "
        "compares them against daily_prices, and writes reports/verify_prices.csv.",
    ),
    (
        "--crosscheck-52w",
        run_crosscheck_command,
        False,
        "Cross-check adjusted prices against official NSE 52-week High/Low files "
        "(CM_52_wk_High_low_DDMMYYYY.csv) for ~10 dates and write reports/adj_crosscheck.csv.",
    ),
]


def build_parser() -> argparse.ArgumentParser:
    """Build the parser: one command flag at a time, plus one optional --from/--to pair."""
    parser = argparse.ArgumentParser(description="NSE end-of-day data pipeline.")
    # A mutually exclusive group makes argparse refuse two commands, instead of the old
    # if/elif chain that ran the first one and silently ignored the rest.
    group = parser.add_mutually_exclusive_group()
    for flag, _function, _uses_dates, help_text in COMMANDS:
        group.add_argument(flag, action="store_true", help=help_text)
    parser.add_argument(
        "--from",
        dest="from_date",
        type=parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="First day of the range (used by --load-prices, --load-mcap, "
        "--download-corporate-actions and --benchmarks)",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Last day of the range (same commands as --from)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    chosen = next((c for c in COMMANDS if getattr(args, c[0][2:].replace("-", "_"))), None)
    if chosen is None:
        # No command given: show the help instead of guessing what to run.
        parser.print_help()
        return
    flag, function, uses_dates, _help = chosen

    if (args.from_date or args.to_date) and not uses_dates:
        parser.error(f"--from/--to cannot be used with {flag}")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error(f"--from {args.from_date} is after --to {args.to_date}")

    setup_logging()  # only here, at the entry point: importing a module must not log to files
    function(args)


if __name__ == "__main__":
    main()
