#!/usr/bin/env python3
"""
CLI Runner for the NSE Bhavcopy ETL Pipeline.
Executes massive historical loads or incremental updates into MySQL.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from config.settings import setup_logging
from src.etl.dry_run import dry_run_is_supported
from src.etl.pipeline import BhavcopyETL

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
        "Symbol changes stored: %d (%d touch our stock_master). Listing master rows: %d.",
        summary["symbol_changes_stored"],
        summary["symbol_changes_touching_universe"],
        summary["listing_rows"],
    )


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
        "--import-universe",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Import stock master and universe from Excel file "
            "(dry-run by default; use --yes to write)"
        ),
    )
    parser.add_argument(
        "--universe-overrides",
        type=str,
        default="config/universe_overrides.csv",
        metavar="PATH",
        help="Path to universe overrides CSV file (default: config/universe_overrides.csv)",
    )
    parser.add_argument(
        "--import-symbol-aliases",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Import symbol aliases from CSV file (default: config/symbol_aliases.csv)",
    )
    parser.add_argument(
        "--apply-symbol-aliases",
        action="store_true",
        help=(
            "Check continuity and apply verified symbol aliases "
            "(dry-run by default; use --yes to write)"
        ),
    )
    parser.add_argument(
        "--detect-renames",
        action="store_true",
        help=(
            "Scan raw bhavcopy cache to detect symbol rename candidates and write "
            "reports/rename_candidates.csv (read-only)"
        ),
    )
    parser.add_argument(
        "--download-corporate-actions",
        action="store_true",
        help=(
            "Download NSE's official corporate actions (splits, bonuses ...), symbol changes "
            "and listing master, keep the raw files in data/corporate_actions/ and upsert "
            "the parsed rows into MySQL. Default window: 10 years back to 90 days ahead; "
            "use --start / --end to change it. Writes to the database (no --dry-run)"
        ),
    )
    parser.add_argument(
        "--add-threshold",
        type=float,
        default=500.0,
        help="Market cap threshold in Crore to add stock to universe (default: 500.0)",
    )
    parser.add_argument(
        "--keep-threshold",
        type=float,
        default=490.0,
        help="Market cap threshold in Crore to retain existing universe stock (default: 490.0)",
    )
    parser.add_argument(
        "--fill-missing-symbols",
        action="store_true",
        help="Fill missing universe symbols across stored trading dates from Bhavcopy cache",
    )
    parser.add_argument(
        "--purge-bogus-sessions",
        action="store_true",
        help="Detect and purge bogus duplicated weekend/holiday sessions from the database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview only, without modifying the database. Supported by --import-universe, "
            "--apply-symbol-aliases, --purge-bogus-sessions, --fill-missing-symbols and "
            "--detect-renames; any other command refuses it"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm writing changes to database "
            "(used with --purge-bogus-sessions, --import-universe, and --apply-symbol-aliases)"
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
            "for trading days from 2024-02-01 onward (Task D6)."
        ),
    )
    parser.add_argument(
        "--verify-prices",
        action="store_true",
        help=(
            "Independent accuracy check (Task D7): re-downloads ~48 trading dates fresh from NSE, "
            "compares them against daily_prices, and writes reports/verify_prices.csv."
        ),
    )
    parser.add_argument(
        "--load-securities",
        action="store_true",
        help=(
            "Import official equity listing master (EQUITY_L.csv) into securities table (Task D8)."
        ),
    )
    parser.add_argument(
        "--load-renames",
        action="store_true",
        help=(
            "Import official symbol change history (symbolchange.csv) into "
            "symbol_changes table (Task D8)."
        ),
    )
    parser.add_argument(
        "--crosscheck-52w",
        action="store_true",
        help=(
            "Cross-check adjusted prices against official NSE 52-week High/Low files "
            "(CM_52_wk_High_low_DDMMYYYY.csv) for ~10 dates and write "
            "reports/adj_crosscheck.csv (Task D10)."
        ),
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Execute the single-command daily update routine (Task D11): load latest prices, "
            "market cap, refresh securities & symbol changes, corporate actions, "
            "prune dry run, adjust prices, and rebuild indicator cache."
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date in YYYY-MM-DD format for --load-prices (default: 2016-11-21)",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date in YYYY-MM-DD format for --load-prices (default: yesterday)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help=(
            "Start date in YYYY-MM-DD format (used with --recheck-non-trading, "
            "--benchmarks, --purge-bogus-sessions, --fill-missing-symbols, --detect-renames, "
            "or --download-corporate-actions)"
        ),
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help=(
            "End date in YYYY-MM-DD format (used with --recheck-non-trading, "
            "--benchmarks, --purge-bogus-sessions, --fill-missing-symbols, --detect-renames, "
            "or --download-corporate-actions)"
        ),
    )

    args = parser.parse_args()

    # Refuse --dry-run for commands that would ignore it and write anyway (issue #46).
    if not dry_run_is_supported(args):
        parser.error(
            "--dry-run is not supported by this command and would write to the database. "
            "Remove --dry-run, or use it with --import-universe, --apply-symbol-aliases, "
            "--purge-bogus-sessions, --fill-missing-symbols or --detect-renames."
        )

    # Single-command daily routine (task D11)
    if args.update:
        from src.etl.daily_update import run_daily_update

        run_daily_update()
        return

    # Official corporate actions do not need the Bhavcopy ETL object, so handle them first.
    if args.download_corporate_actions:
        run_corporate_actions_command(args)
        return

    # Ingest raw Bhavcopy prices (task D5)
    if args.load_prices:
        from src.ingestion.price_loader import load_raw_prices

        start_str = args.from_date or args.start
        end_str = args.to_date or args.end
        load_raw_prices(start=start_str, end=end_str)
        return

    # Ingest market cap from PR bundles (task D6)
    if args.load_mcap:
        from src.ingestion.mcap_loader import load_market_caps

        start_str = args.from_date or args.start
        end_str = args.to_date or args.end
        load_market_caps(start=start_str, end=end_str)
        return

    # Independent price accuracy check (task D7)
    if args.verify_prices:
        from scripts.verify_prices import verify_prices

        diff_count, _ = verify_prices()
        if diff_count > 0:
            logger.error("Price verification failed with %d discrepancies.", diff_count)
            sys.exit(1)
        logger.info("Price verification succeeded with 0 discrepancies.")
        return

    # Ingest securities master from EQUITY_L.csv (task D8)
    if args.load_securities:
        from src.ingestion.securities import import_equity_l

        count = import_equity_l()
        logger.info("Imported %d securities into `securities` table.", count)
        return

    # Ingest symbol changes from symbolchange.csv (task D8)
    if args.load_renames:
        from src.ingestion.renames import load_renames

        count = load_renames()
        logger.info("Imported %d symbol changes into `symbol_changes` table.", count)
        return

    # Official 52-week cross-check (task D10)
    if args.crosscheck_52w:
        from scripts.crosscheck_52w import crosscheck_52w

        res = crosscheck_52w()
        logger.info("52-week cross-check completed with %d comparisons.", len(res))
        return

    logger.info("Initializing Bhavcopy ETL pipeline...")
    etl = BhavcopyETL(
        initial_days_back=args.days_back,
        download_delay=args.delay,
        batch_size=args.batch_size,
    )

    try:
        if args.import_universe:
            file_path = Path(args.import_universe)
            if not file_path.exists():
                logger.error("Universe file does not exist: %s", file_path)
                sys.exit(1)
            dry_run = args.dry_run or (not args.yes)
            if dry_run and not args.dry_run:
                logger.info(
                    "--import-universe invoked without --yes: defaulting to --dry-run mode."
                )
            logger.info(
                "Importing universe from %s (add_th=%.1f, keep_th=%.1f, dry_run=%s)...",
                file_path,
                args.add_threshold,
                args.keep_threshold,
                dry_run,
            )
            res = etl.import_universe(
                file_path=file_path,
                add_threshold=args.add_threshold,
                keep_threshold=args.keep_threshold,
                dry_run=dry_run,
                overrides_path=args.universe_overrides,
            )
            logger.info(
                "Universe import [%s] complete: %d active, %d below threshold, %d not in file.",
                "DRY RUN" if res.get("dry_run") else "SAVED",
                res.get("active_count", 0),
                res.get("below_threshold_count", 0),
                res.get("not_in_file_count", 0),
            )
        elif args.import_symbol_aliases is not None:
            alias_path = Path(args.import_symbol_aliases) if args.import_symbol_aliases else None
            res = etl.import_symbol_aliases(file_path=alias_path)
            logger.info(
                "Import symbol aliases complete: %d candidate records imported.",
                res.get("imported", 0),
            )
        elif args.apply_symbol_aliases:
            dry_run = args.dry_run or (not args.yes)
            if dry_run and not args.dry_run:
                logger.info(
                    "--apply-symbol-aliases invoked without --yes: defaulting to --dry-run mode."
                )
            logger.info("Executing apply symbol aliases (dry_run=%s)...", dry_run)
            res = etl.apply_symbol_aliases(dry_run=dry_run)
            logger.info(
                "Apply symbol aliases [%s] complete: %d applied.",
                "DRY RUN" if dry_run else "APPLIED",
                res.get("applied_count", 0),
            )
        elif args.detect_renames:
            start_date = None
            end_date = None
            if args.start:
                try:
                    start_date = date.fromisoformat(args.start)
                except ValueError as val_err:
                    logger.error("Invalid date format for --start: %s", val_err)
                    sys.exit(1)
            if args.end:
                try:
                    end_date = date.fromisoformat(args.end)
                except ValueError as val_err:
                    logger.error("Invalid date format for --end: %s", val_err)
                    sys.exit(1)
            if start_date and end_date and start_date > end_date:
                logger.error(
                    "--start date (%s) cannot be after --end date (%s)", start_date, end_date
                )
                sys.exit(1)

            logger.info(
                "Executing detect symbol renames (start=%s, end=%s)...",
                start_date,
                end_date,
            )
            res = etl.detect_renames(start=start_date, end=end_date)
            logger.info(
                "Detect renames complete: %d candidates, %d ambiguous. Report saved to %s",
                res.get("candidates_count", 0),
                res.get("ambiguous_count", 0),
                res.get("report_path", ""),
            )
        elif args.fill_missing_symbols:
            start_date = None
            end_date = None
            if args.start:
                try:
                    start_date = date.fromisoformat(args.start)
                except ValueError as val_err:
                    logger.error("Invalid date format for --start: %s", val_err)
                    sys.exit(1)
            if args.end:
                try:
                    end_date = date.fromisoformat(args.end)
                except ValueError as val_err:
                    logger.error("Invalid date format for --end: %s", val_err)
                    sys.exit(1)
            if start_date and end_date and start_date > end_date:
                logger.error(
                    "--start date (%s) cannot be after --end date (%s)", start_date, end_date
                )
                sys.exit(1)

            logger.info(
                "Executing fill missing universe symbols (start=%s, end=%s)...",
                start_date,
                end_date,
            )
            res = etl.fill_missing_symbols(start=start_date, end=end_date, dry_run=args.dry_run)
            logger.info(
                "Fill missing symbols %s: %d dates, %d rows %s across %d symbols.",
                "[DRY RUN] complete" if args.dry_run else "complete",
                res.get("dates_processed", 0),
                res.get("rows_added", 0),
                "would be added" if args.dry_run else "added",
                res.get("symbols_added", 0),
            )
        elif args.refresh_cache:
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
        elif args.purge_bogus_sessions:
            start_date = None
            end_date = None
            if args.start:
                try:
                    start_date = date.fromisoformat(args.start)
                except ValueError as val_err:
                    logger.error("Invalid date format for --start: %s", val_err)
                    sys.exit(1)
            if args.end:
                try:
                    end_date = date.fromisoformat(args.end)
                except ValueError as val_err:
                    logger.error("Invalid date format for --end: %s", val_err)
                    sys.exit(1)
            if start_date and end_date and start_date > end_date:
                logger.error(
                    "--start date (%s) cannot be after --end date (%s)", start_date, end_date
                )
                sys.exit(1)

            dry_run = args.dry_run or (not args.yes)
            if dry_run and not args.dry_run:
                logger.info(
                    "--purge-bogus-sessions invoked without --yes: defaulting to --dry-run mode."
                )

            logger.info(
                "Executing bogus session purge (start=%s, end=%s, dry_run=%s, confirm=%s)...",
                start_date,
                end_date,
                dry_run,
                args.yes,
            )
            result = etl.purge_bogus_sessions(
                start=start_date,
                end=end_date,
                dry_run=dry_run,
                confirm=args.yes,
            )
            logger.info(
                "Purge completed [%s]. Bogus sessions: %d",
                "DRY RUN" if result.get("dry_run") else "PURGED",
                result.get("bogus_count", 0),
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
