#!/usr/bin/env python3
"""
CLI Runner for the NSE Bhavcopy ETL Pipeline.
Executes massive historical loads or incremental updates into MySQL.
"""

import argparse
import logging
import sys

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
        "--lookback",
        type=int,
        default=5,
        help="Number of days to check for incremental update if no prior data exists (default: 5)",
    )
    parser.add_argument(
        "--rebuild-adjusted",
        action="store_true",
        help="Recompute corporate action price adjustments (splits/bonuses) and update database",
    )
    parser.add_argument(
        "--benchmarks",
        action="store_true",
        help="Download and ingest benchmark index prices standalone",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Rebuild the pre-computed indicator Parquet cache from MySQL without downloading",
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
        elif args.benchmarks:
            logger.info("Executing standalone benchmark index ingestion...")
            count = etl.ingest_benchmarks()
            logger.info("Benchmark ingestion complete. Ingested: %d records.", count)
        elif args.incremental:
            logger.info("Executing incremental ETL update...")
            res = etl.run_incremental(lookback_days=args.lookback)
            logger.info(
                "Incremental update completed [%s]: %s (updated: %d symbols)",
                res.get("status"),
                res.get("message"),
                res.get("symbols_updated", 0),
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
