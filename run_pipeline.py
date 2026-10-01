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

    args = parser.parse_args()

    logger.info("Initializing Bhavcopy ETL pipeline...")
    etl = BhavcopyETL(
        initial_days_back=args.days_back,
        download_delay=args.delay,
        batch_size=args.batch_size,
    )

    try:
        success, failed = etl.run(skip_constituent_filter=args.skip_constituents)
        logger.info(
            "ETL Pipeline execution completed. Successfully loaded: %d symbols, Failed: %d symbols.",
            len(success),
            len(failed),
        )
    except Exception as exc:
        logger.error("Pipeline failed with error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
