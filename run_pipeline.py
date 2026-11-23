#!/usr/bin/env python3
"""
CLI runner for the NSE data pipeline.

Every data operation is one flag. For the daily routine just run:

    python run_pipeline.py --update

Run it with --help to see all commands.
"""

import argparse
import logging
import sys
from datetime import date

from config.settings import NSE_HISTORY_START, setup_logging

setup_logging()
logger = logging.getLogger("run_pipeline")


def run_corporate_actions_command(args: argparse.Namespace) -> None:
    """Run --download-corporate-actions and print a short summary for the owner."""
    from src.ingestion.corporate_actions import NSEDownloadError, NSEFormatChangedError
    from src.ingestion.corporate_actions_store import run_corporate_actions_download

    start_date = date.fromisoformat(args.start) if args.start else None
    end_date = date.fromisoformat(args.end) if args.end else None
    try:
        summary = run_corporate_actions_download(start=start_date, end=end_date)
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


def run_daily_update_command() -> None:
    """Run --update. A failed step is reported in one clear line and exits non-zero."""
    from src.etl.daily_update import DailyUpdateError, run_daily_update

    try:
        run_daily_update()
    except DailyUpdateError as exc:
        logger.error("%s", exc)
        sys.exit(1)


def parse_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "The daily routine: load new prices, market cap, securities and symbol changes, "
            "corporate actions, check bogus sessions (report only), refresh benchmarks, adjust "
            "prices and rebuild the indicator cache. Stops at the first failing step."
        ),
    )
    parser.add_argument(
        "--load-prices",
        action="store_true",
        help=(
            "Ingest official raw Bhavcopy prices (all regular trading series: EQ, BE, BZ, SM, ST) "
            "from NSE archives into daily_prices. Resumable, polite, and verified."
        ),
    )
    parser.add_argument(
        "--load-mcap",
        action="store_true",
        help=(
            "Fill daily_prices.market_cap from official NSE PR bundles (PRDDMMYY.zip) "
            "for trading days from 2024-02-01 onward."
        ),
    )
    parser.add_argument(
        "--load-securities",
        action="store_true",
        help="Import the official equity listing master (EQUITY_L.csv) into securities.",
    )
    parser.add_argument(
        "--load-renames",
        action="store_true",
        help="Import official symbol change history (symbolchange.csv) into symbol_changes.",
    )
    parser.add_argument(
        "--download-corporate-actions",
        action="store_true",
        help=(
            "Download NSE's official corporate actions (splits, bonuses ...), symbol changes "
            "and listing master, keep the raw files in data/corporate_actions/ and upsert "
            "the parsed rows into MySQL. Default window: 10 years back to 90 days ahead; "
            "use --start / --end to change it."
        ),
    )
    parser.add_argument(
        "--rebuild-adjusted",
        action="store_true",
        help=(
            "Recompute corporate action price adjustments, update the database and refresh "
            "the indicator cache. Run it after every corporate-actions download."
        ),
    )
    parser.add_argument(
        "--benchmarks",
        action="store_true",
        help=(
            "Download benchmark index closes for --start to --end (default: 2016-11-21 to "
            "today) and refresh the indicator cache. The daily --update already does this "
            "for new days."
        ),
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Rebuild the pre-computed indicator Parquet cache from MySQL without downloading",
    )
    parser.add_argument(
        "--verify-prices",
        action="store_true",
        help=(
            "Independent accuracy check: re-downloads ~48 trading dates fresh from NSE, "
            "compares them against daily_prices, and writes reports/verify_prices.csv."
        ),
    )
    parser.add_argument(
        "--crosscheck-52w",
        action="store_true",
        help=(
            "Cross-check adjusted prices against official NSE 52-week High/Low files "
            "(CM_52_wk_High_low_DDMMYYYY.csv) for ~10 dates and write "
            "reports/adj_crosscheck.csv."
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date for --load-prices and --load-mcap (default: 2016-11-21)",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date for --load-prices and --load-mcap (default: yesterday)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date YYYY-MM-DD (used with --benchmarks or --download-corporate-actions)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date YYYY-MM-DD (used with --benchmarks or --download-corporate-actions)",
    )
    return parser.parse_args()


def run_benchmarks_command(args: argparse.Namespace) -> None:
    """Run --benchmarks: load index closes for the range, then refresh the cache."""
    from src.ingestion.benchmark import load_benchmark_range

    start_date = date.fromisoformat(args.start) if args.start else NSE_HISTORY_START
    end_date = date.fromisoformat(args.end) if args.end else date.today()
    saved = load_benchmark_range(start_date, end_date)
    logger.info("Benchmark ingestion complete. Ingested: %d records.", saved)
    refresh_indicator_cache()


def run_rebuild_adjusted_command() -> None:
    """Run --rebuild-adjusted: recompute adjusted prices, then refresh the cache."""
    from src.etl.adjust import rebuild_adjusted

    updated = rebuild_adjusted()
    logger.info("Adjusted prices rebuild complete. Updated: %d rows.", updated)
    refresh_indicator_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="NSE end-of-day data pipeline.")
    args = parse_args(parser)

    if args.update:
        run_daily_update_command()
    elif args.download_corporate_actions:
        run_corporate_actions_command(args)
    elif args.load_prices:
        from src.ingestion.price_loader import load_raw_prices

        load_raw_prices(start=args.from_date or args.start, end=args.to_date or args.end)
    elif args.load_mcap:
        from src.ingestion.mcap_loader import load_market_caps

        load_market_caps(start=args.from_date or args.start, end=args.to_date or args.end)
    elif args.verify_prices:
        from scripts.verify_prices import verify_prices

        diff_count, _ = verify_prices()
        if diff_count > 0:
            logger.error("Price verification failed with %d discrepancies.", diff_count)
            sys.exit(1)
        logger.info("Price verification succeeded with 0 discrepancies.")
    elif args.load_securities:
        from src.ingestion.securities import import_equity_l

        logger.info("Imported %d securities into `securities` table.", import_equity_l())
    elif args.load_renames:
        from src.ingestion.renames import load_renames

        logger.info("Imported %d symbol changes into `symbol_changes` table.", load_renames())
    elif args.crosscheck_52w:
        from scripts.crosscheck_52w import crosscheck_52w

        logger.info("52-week cross-check completed with %d comparisons.", len(crosscheck_52w()))
    elif args.rebuild_adjusted:
        run_rebuild_adjusted_command()
    elif args.benchmarks:
        run_benchmarks_command(args)
    elif args.refresh_cache:
        refresh_indicator_cache()
    else:
        # No command given: show the help instead of guessing what to run.
        parser.print_help()


if __name__ == "__main__":
    main()
