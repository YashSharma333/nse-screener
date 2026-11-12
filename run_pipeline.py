#!/usr/bin/env python3
"""
CLI Runner for the NSE Bhavcopy ETL Pipeline.
Executes massive historical loads or incremental updates into MySQL.
"""

import argparse
import logging
import sys
from datetime import date

from config.settings import setup_logging
from src.etl.pipeline import BhavcopyETL

setup_logging()
logger = logging.getLogger("run_pipeline")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run NSE Bhavcopy historical ingestion and ETL pipeline."
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=1100,
        help="Number of calendar days to look back on initial load (default: 1100, ~3 years)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Polite download delay in seconds between requests (default: 0.8)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Database upsert batch size (default: 1000)",
    )
    parser.add_argument(
        "--skip-constituents",
        action="store_true",
        help="Bypass NIFTY constituent filtering to ingest all EQ-series symbols",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Run incremental update for recent days only instead of full backfill",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill missing historical dates within --days-back window in bounded chunks",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Number of candidate days per backfill chunk (default: 100)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=5,
        help="Number of days to check for incremental update if no prior data exists (default: 5)",
    )
    parser.add_argument(
        "--rebuild-adjusted",
        action="store_true",
        help=(
            "Recompute corporate action price adjustments (splits/bonuses), "
            "update database, and refresh indicator cache"
        ),
    )
    parser.add_argument(
        "--benchmarks",
        action="store_true",
        help="Download and ingest benchmark index prices standalone, and refresh indicator cache",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Rebuild the pre-computed indicator Parquet cache from MySQL without downloading",
    )
    parser.add_argument(
        "--recheck-non-trading",
        action="store_true",
        help="Clear and re-probe cached non-trading days within --start and --end range",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start date in YYYY-MM-DD format (used with --recheck-non-trading or --benchmarks)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format (used with --recheck-non-trading or --benchmarks)",
    )

    args = parser.parse_args()

    logger.info("Initializing Bhavcopy ETL pipeline...")
    etl = BhavcopyETL(
        initial_days_back=args.days_back,
        download_delay=args.delay,
        batch_size=args.batch_size,
    )

    try:
        if args.refresh_cache:
            logger.info("Rebuilding indicator Parquet cache from MySQL...")
            from src.db.data_service import load_indicator_snapshot

            snapshot, _full, is_live, msg = load_indicator_snapshot(
                use_adjusted=True, force_refresh=True
            )
            logger.info(
                "Indicator cache rebuilt: %d symbols, live=%s (%s)",
                len(snapshot),
                is_live,
                msg,
            )
        elif args.rebuild_adjusted:
            logger.info("Executing standalone corporate action price adjustments...")
            updated = etl.rebuild_adjusted_prices()
            logger.info("Adjusted prices rebuild complete. Updated: %d rows.", updated)
            logger.info("Refreshing indicator cache after adjustment rebuild...")
            from src.db.data_service import load_indicator_snapshot

            load_indicator_snapshot(use_adjusted=True, force_refresh=True)
            logger.info("Indicator cache refreshed successfully.")
        elif args.recheck_non_trading:
            if not args.start or not args.end:
                logger.error("--recheck-non-trading requires both --start and --end (YYYY-MM-DD)")
                sys.exit(1)
            try:
                start_date = date.fromisoformat(args.start)
                end_date = date.fromisoformat(args.end)
            except ValueError as val_err:
                logger.error("Invalid date format for --start/--end: %s", val_err)
                sys.exit(1)
            if start_date > end_date:
                logger.error(
                    "--start date (%s) cannot be after --end date (%s)", start_date, end_date
                )
                sys.exit(1)

            logger.info(
                "Executing recheck of cached non-trading days (%s → %s)...",
                start_date,
                end_date,
            )
            success, failed = etl.recheck_non_trading(
                start=start_date,
                end=end_date,
                skip_constituent_filter=args.skip_constituents,
            )
            logger.info(
                "Recheck completed. Loaded: %d symbols, Failed: %d symbols.",
                len(success),
                len(failed),
            )
        elif args.benchmarks:
            logger.info("Executing standalone benchmark index ingestion...")
            start_date = date.fromisoformat(args.start) if args.start else None
            end_date = date.fromisoformat(args.end) if args.end else None
            count = etl.ingest_benchmarks(start_date=start_date, end_date=end_date)
            logger.info("Benchmark ingestion complete. Ingested: %d records.", count)
            logger.info("Refreshing indicator cache after benchmark ingestion...")
            from src.db.data_service import load_indicator_snapshot

            load_indicator_snapshot(use_adjusted=True, force_refresh=True)
            logger.info("Indicator cache refreshed successfully.")
        elif args.incremental:
            logger.info("Executing incremental ETL update...")
            res = etl.run_incremental(lookback_days=args.lookback)
            logger.info(
                "Incremental update completed [%s]: %s (updated: %d symbols)",
                res.get("status"),
                res.get("message"),
                res.get("symbols_updated", 0),
            )
        elif args.backfill:
            logger.info(
                "Executing backwards backfill mode (days_back=%d, chunk_size=%d)...",
                args.days_back,
                args.chunk_size,
            )
            success, failed = etl.run_backfill(
                days_back=args.days_back,
                chunk_days=args.chunk_size,
                skip_constituent_filter=args.skip_constituents,
            )
            logger.info(
                "Backfill completed. Loaded: %d symbols, Failed: %d symbols.",
                len(success),
                len(failed),
            )
        else:
            success, failed = etl.run(skip_constituent_filter=args.skip_constituents)
            logger.info(
                "ETL Pipeline completed. Loaded: %d symbols, Failed: %d symbols.",
                len(success),
                len(failed),
            )
    except Exception as exc:
        logger.error("Pipeline failed with error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
