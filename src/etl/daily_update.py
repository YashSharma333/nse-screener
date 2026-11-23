"""
Daily Routine Orchestrator (Task D11)
=====================================
Provides `run_daily_update()`: a single, idempotent routine running the 9 daily steps
in strict sequential order, stopping at the first failure with a clear error report.

Steps in order:
 1. load_prices from the day after the last loaded date to yesterday (D5 logic)
 2. market cap for the new dates (D6)
 3. refresh EQUITY_L and symbolchange.csv (D8)
 4. refresh corporate actions (D10)
 5. check for bogus duplicate sessions: report only, nothing is ever deleted
 6. refresh benchmark index closes (needed by the relative-strength indicators)
 7. rebuild adjusted prices for symbols with new events or new rows
 8. rebuild the indicator cache (rebuilds itself when DB max date changed)
 9. print one summary: new dates, rows added, new/renamed/dropped symbols, warnings

Each step commits its own work when it succeeds. A failed step rolls back only itself:
the committed work of the earlier steps stays in the database.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import FIRST_MCAP_DATE, NSE_HISTORY_START
from src.db.models import DailyPrice, Security, SymbolChange
from src.db.session import get_db
from src.ingestion.nse_files import fetch

logger = logging.getLogger(__name__)


class DailyUpdateError(RuntimeError):
    """Raised when a daily update step fails, identifying the failing step."""

    def __init__(self, step_number: int, step_name: str, original_error: Exception | str) -> None:
        self.step_number = step_number
        self.step_name = step_name
        self.original_error = original_error
        super().__init__(
            f"Daily update failed at Step {step_number} ({step_name}): {original_error}"
        )


@dataclass
class DailyUpdateSummary:
    """Summary of actions and statistics produced by a daily update run."""

    new_dates: list[date] = field(default_factory=list)
    rows_added: int = 0
    mcap_rows_updated: int = 0
    new_symbols: list[str] = field(default_factory=list)
    renamed_symbols: list[tuple[str, str]] = field(default_factory=list)
    dropped_symbols: list[str] = field(default_factory=list)
    adjusted_rows: int = 0
    bogus_sessions_flagged: int = 0
    price_gaps: list[date] = field(default_factory=list)
    benchmark_rows_saved: int = 0
    warnings: list[str] = field(default_factory=list)
    step_results: dict[str, Any] = field(default_factory=dict)

    def format_text(self) -> str:
        """Format a clean, readable terminal summary."""
        dates_str = (
            ", ".join(d.isoformat() for d in self.new_dates)
            if self.new_dates
            else "None (database already up to date)"
        )
        first_5_new = ", ".join(self.new_symbols[:5])
        more_new = "..." if len(self.new_symbols) > 5 else ""
        new_sym_str = (
            f"{len(self.new_symbols)} ({first_5_new}{more_new})" if self.new_symbols else "0"
        )
        rn_tuples = [f"{o}->{n}" for o, n in self.renamed_symbols[:5]]
        renamed_str = (
            f"{len(self.renamed_symbols)} ({', '.join(rn_tuples)})" if self.renamed_symbols else "0"
        )
        dropped_str = f"{len(self.dropped_symbols)}" if self.dropped_symbols else "0"
        warn_str = "\n".join(f"  - {w}" for w in self.warnings) if self.warnings else "  None"

        return (
            "\n"
            "====================================================================\n"
            "                     DAILY UPDATE SUMMARY                     \n"
            "====================================================================\n"
            f"• New trading dates loaded : {dates_str}\n"
            f"• Price rows inserted      : {self.rows_added:,}\n"
            f"• Market cap rows updated  : {self.mcap_rows_updated:,}\n"
            f"• Active securities added  : {new_sym_str}\n"
            f"• Symbol renames tracked   : {renamed_str}\n"
            f"• Inactive/dropped symbols : {dropped_str}\n"
            f"• Benchmark rows saved     : {self.benchmark_rows_saved:,}\n"
            f"• Adjusted rows recomputed : {self.adjusted_rows:,}\n"
            f"• Bogus sessions flagged   : {self.bogus_sessions_flagged} (report only, 0 deleted)\n"
            f"• Price gaps remaining     : {len(self.price_gaps)} (see reports/price_gaps.csv)\n"
            f"• Warnings & notices:\n{warn_str}\n"
            "===================================================================="
        )


STEP_NAMES = {
    1: "load_prices",
    2: "market_cap",
    3: "refresh_securities_and_renames",
    4: "refresh_corporate_actions",
    5: "check_bogus_sessions",
    6: "refresh_benchmarks",
    7: "rebuild_adjusted_prices",
    8: "rebuild_indicator_cache",
    9: "summary",
}


def _retry_price_gaps(s: Session, fetch_fn: Callable[[str], bytes], ref_today: date) -> None:
    """Retry every calendar day with no prices that is not a confirmed holiday.

    Why: step 1 only starts at max(date)+1, so a day that failed once (for example an
    HTML block page) would never be visited again.
    """
    from src.ingestion.price_loader import find_price_gaps, load_raw_prices

    for gap_day in find_price_gaps(s):
        logger.info("Retrying price gap %s...", gap_day)
        load_raw_prices(
            start=gap_day, end=gap_day, session=s, fetch_fn=fetch_fn, ref_date=ref_today
        )


def run_daily_update(
    session: Session | None = None,
    fetch_fn: Callable[[str], bytes] = fetch,
    today: date | None = None,
    step_handlers: dict[int, Callable[..., Any]] | None = None,
) -> DailyUpdateSummary:
    """Execute the daily update routine in order, stopping at the first failure.

    Parameters
    ----------
    session : Session, optional
        Database session. If None, acquires managed session via get_db().
    fetch_fn : Callable[[str], bytes], default fetch
        Network download helper (injected for offline unit testing).
    today : date, optional
        Reference date for yesterday boundary (default: date.today()).
    step_handlers : dict[int, Callable], optional
        Override dictionary for individual steps (used in tests to record order or mock failures).

    Returns
    -------
    DailyUpdateSummary
        Aggregated summary of newly loaded data and warnings.

    Raises
    ------
    DailyUpdateError
        If any step fails, stopping execution and identifying the failed step.
    """
    ref_today = today or date.today()
    yesterday = ref_today - timedelta(days=1)
    summary = DailyUpdateSummary()
    handlers = step_handlers or {}

    def _execute(s: Session) -> DailyUpdateSummary:
        # Helper to execute step with fail-fast error wrapping
        def _run_step(step_num: int, default_fn: Callable[[], Any]) -> Any:
            step_name = STEP_NAMES[step_num]
            logger.info("Starting Daily Update Step %d: %s...", step_num, step_name)
            handler = handlers.get(step_num, default_fn)
            try:
                res = handler()
                # KEEP: commit per step. Step 8 reads through its own connection; under MySQL
                # REPEATABLE READ it would not see this session's uncommitted writes and could
                # build the indicator cache from OLD data.
                s.commit()
                summary.step_results[step_name] = res
                return res
            except Exception as exc:
                # A failed step undoes only its own half-written work; earlier steps stay saved.
                s.rollback()
                logger.error("Failed Daily Update Step %d (%s): %s", step_num, step_name, exc)
                raise DailyUpdateError(step_num, step_name, exc) from exc

        # ---------------------------------------------------------------------
        # Step 1: load_prices from day after last loaded date to yesterday (D5)
        # ---------------------------------------------------------------------
        def _step_1() -> dict[str, Any]:
            from src.ingestion.price_loader import find_price_gaps, write_price_gaps_report

            # Old holes first, then new days; the report shows what is still missing.
            _retry_price_gaps(s, fetch_fn, ref_today)
            result = _load_new_days()
            summary.price_gaps = find_price_gaps(s)
            write_price_gaps_report(summary.price_gaps)
            if summary.price_gaps:
                summary.warnings.append(
                    f"{len(summary.price_gaps)} calendar days have no prices and are not "
                    "confirmed holidays (reports/price_gaps.csv)."
                )
            return result

        def _load_new_days() -> dict[str, Any]:
            max_date = s.scalar(select(func.max(DailyPrice.date)))
            start_date = max_date + timedelta(days=1) if max_date else NSE_HISTORY_START
            if start_date > yesterday:
                logger.info(
                    "Latest date in DB (%s) is already up to date with yesterday (%s).",
                    max_date,
                    yesterday,
                )
                return {"new_dates": [], "rows_added": 0}

            from src.ingestion.price_loader import load_raw_prices

            logger.info(
                "Loading raw Bhavcopy prices from %s to %s...",
                start_date,
                yesterday,
            )
            load_summary = load_raw_prices(
                start=start_date,
                end=yesterday,
                session=s,
                fetch_fn=fetch_fn,
                ref_date=ref_today,
            )

            # Query which dates were newly inserted
            stmt = (
                select(DailyPrice.date)
                .distinct()
                .where(DailyPrice.date >= start_date, DailyPrice.date <= yesterday)
                .order_by(DailyPrice.date)
            )
            loaded_dates = list(s.scalars(stmt).all())
            summary.new_dates = loaded_dates
            summary.rows_added = load_summary.rows_inserted
            if load_summary.bad_file > 0:
                summary.warnings.append(
                    f"{load_summary.bad_file} corrupt or empty Bhavcopy files encountered."
                )
            return {"new_dates": loaded_dates, "rows_added": load_summary.rows_inserted}

        _run_step(1, _step_1)

        # ---------------------------------------------------------------------
        # Step 2: market cap for the new dates (D6)
        # ---------------------------------------------------------------------
        def _step_2() -> dict[str, Any]:
            from src.ingestion.mcap_loader import load_market_caps

            # Target dates: either newly loaded dates or any dates >= FIRST_MCAP_DATE missing mcap
            target_dates = [d for d in summary.new_dates if d >= FIRST_MCAP_DATE]
            if not target_dates:
                # If no new dates, check if any recent trading dates in DB lack market cap
                recent_check = s.scalars(
                    select(DailyPrice.date)
                    .distinct()
                    .where(
                        DailyPrice.date >= max(FIRST_MCAP_DATE, yesterday - timedelta(days=7)),
                        DailyPrice.date <= yesterday,
                        DailyPrice.market_cap.is_(None),
                    )
                    .order_by(DailyPrice.date)
                ).all()
                target_dates = list(recent_check)

            if not target_dates:
                logger.info("No new dates requiring market cap load.")
                return {"updated_rows": 0, "unmatched": 0}

            m_start = min(target_dates)
            m_end = max(target_dates)
            logger.info("Loading market cap from PR bundles for dates %s to %s...", m_start, m_end)
            mcap_sum = load_market_caps(
                start=m_start,
                end=m_end,
                session=s,
                fetch_fn=fetch_fn,
            )
            summary.mcap_rows_updated = mcap_sum.updated_rows
            if mcap_sum.unmatched_symbols > 0:
                summary.warnings.append(
                    f"{mcap_sum.unmatched_symbols} PR bundle market cap symbols were "
                    "not in daily_prices."
                )
            return {"updated_rows": mcap_sum.updated_rows, "unmatched": mcap_sum.unmatched_symbols}

        _run_step(2, _step_2)

        # ---------------------------------------------------------------------
        # Step 3: refresh EQUITY_L and symbolchange.csv (D8)
        # ---------------------------------------------------------------------
        def _step_3() -> dict[str, Any]:
            from src.ingestion.renames import load_renames
            from src.ingestion.securities import import_equity_l

            pre_active = set(
                s.scalars(select(Security.symbol).where(Security.status == "active")).all()
            )
            pre_renames_count = s.scalar(select(func.count(SymbolChange.old_symbol))) or 0

            eq_count = import_equity_l(session=s, fetch_fn=fetch_fn)
            rn_count = load_renames(session=s, fetch_fn=fetch_fn)

            post_active = set(
                s.scalars(select(Security.symbol).where(Security.status == "active")).all()
            )
            new_syms = sorted(post_active - pre_active)
            dropped_syms = sorted(
                s.scalars(select(Security.symbol).where(Security.status != "active")).all()
            )

            # Discover newly added renames
            new_renames: list[tuple[str, str]] = []
            if rn_count > pre_renames_count:
                new_renames_stmt = (
                    select(SymbolChange.old_symbol, SymbolChange.new_symbol)
                    .order_by(SymbolChange.effective_date.desc(), SymbolChange.old_symbol.desc())
                    .limit(rn_count - pre_renames_count)
                )
                new_renames = list(s.execute(new_renames_stmt).all())

            summary.new_symbols = new_syms
            summary.dropped_symbols = dropped_syms
            summary.renamed_symbols = [(r[0], r[1]) for r in new_renames]

            logger.info(
                "Refreshed securities (%d rows) and renames (%d rows). "
                "New active: %d, Dropped: %d, New renames: %d",
                eq_count,
                rn_count,
                len(new_syms),
                len(dropped_syms),
                len(new_renames),
            )
            return {
                "equity_l_rows": eq_count,
                "renames_rows": rn_count,
                "new_active": len(new_syms),
            }

        _run_step(3, _step_3)

        # ---------------------------------------------------------------------
        # Step 4: refresh corporate actions (D10)
        # ---------------------------------------------------------------------
        def _step_4() -> dict[str, Any]:
            from src.ingestion.corporate_actions_store import run_corporate_actions_download

            ca_res = run_corporate_actions_download(session=s, fetch_fn=fetch_fn, today=ref_today)
            stored_rows = ca_res.get("rows_stored", 0)
            exact_factors = ca_res.get("exact_factor_events", 0)
            needs_review = ca_res.get("needs_review", 0)

            logger.info(
                "Refreshed corporate actions: %d stored, %d exact factors, %d to review.",
                stored_rows,
                exact_factors,
                needs_review,
            )
            if needs_review > 0:
                summary.warnings.append(
                    f"{needs_review} corporate action events need owner review in "
                    "reports/adj_events_uncertain.csv."
                )
            return ca_res

        _run_step(4, _step_4)

        # ---------------------------------------------------------------------
        # Step 5: check for bogus sessions (report only; --update never deletes)
        # ---------------------------------------------------------------------
        def _step_5() -> dict[str, Any]:
            from src.etl.bogus_sessions import report_bogus_sessions

            # Report only: this module has no code that deletes rows, so --update can
            # never remove price history.
            result = report_bogus_sessions(session=s)
            bogus_count = result.get("bogus_count", 0)
            summary.bogus_sessions_flagged = bogus_count

            if bogus_count > 0:
                flagged_dates = [str(d) for d in result.get("flagged_dates", [])]
                msg = (
                    f"{bogus_count} bogus duplicate/holiday session(s) detected: {flagged_dates}. "
                    "Report only; no rows were deleted."
                )
                logger.warning(msg)
                summary.warnings.append(msg)
            else:
                logger.info("Prune check: 0 bogus sessions detected.")

            return result

        _run_step(5, _step_5)

        # ---------------------------------------------------------------------
        # Step 6: refresh benchmark index closes (feed relative-strength indicators)
        # ---------------------------------------------------------------------
        def _step_6() -> dict[str, Any]:
            from src.db.models import BenchmarkPrice
            from src.ingestion.benchmark import load_benchmarks, missing_benchmark_dates

            last_bench = s.scalar(select(func.max(BenchmarkPrice.date)))
            if last_bench is None:
                msg = (
                    "No benchmark history stored; run "
                    "`python run_pipeline.py --benchmarks --start 2016-11-21` once."
                )
                logger.warning(msg)
                summary.warnings.append(msg)
                return {"saved_rows": 0}

            # Every price date without a benchmark row (not just dates after the last one),
            # so a day missed in the middle is retried. Dates NSE has no file for are
            # remembered as non-trading and skipped.
            new_dates = [d for d in missing_benchmark_dates(s) if d <= yesterday]
            if not new_dates:
                logger.info("Benchmarks have no missing dates (last %s).", last_bench)
                return {"saved_rows": 0}

            saved = load_benchmarks(new_dates, session=s)
            summary.benchmark_rows_saved = saved
            return {"saved_rows": saved}

        _run_step(6, _step_6)

        # ---------------------------------------------------------------------
        # Step 7: rebuild adjusted prices for symbols with new events or new rows
        # ---------------------------------------------------------------------
        def _step_7() -> dict[str, Any]:
            from src.etl.adjust import adjust_new_rows, adjust_rows_with_missing_adjusted

            # 1. If new trading dates were added, retrieve those rows and incrementally adjust
            updated_count = 0
            if summary.new_dates:
                stmt = select(DailyPrice).where(DailyPrice.date.in_(summary.new_dates))
                new_price_rows = s.scalars(stmt).all()
                if new_price_rows:
                    df_new = pd.DataFrame(
                        [
                            {
                                "symbol": r.symbol,
                                "date": r.date,
                                "open": float(r.open) if r.open is not None else None,
                                "high": float(r.high) if r.high is not None else None,
                                "low": float(r.low) if r.low is not None else None,
                                "close": float(r.close) if r.close is not None else None,
                                "volume": int(r.volume) if r.volume is not None else None,
                            }
                            for r in new_price_rows
                        ]
                    )
                    updated_count = adjust_new_rows(df_new, session=s)
                    logger.info("Adjusted new price rows: %d rows updated.", updated_count)
            else:
                logger.info("No new price dates loaded.")

            # Also repair rows an earlier failed run left with NULL adj_* (they are not "new").
            updated_count += adjust_rows_with_missing_adjusted(s)

            summary.adjusted_rows = updated_count
            return {"updated_rows": updated_count}

        _run_step(7, _step_7)

        # ---------------------------------------------------------------------
        # Step 8: rebuild indicator cache (rebuilds when DB max date changed)
        # ---------------------------------------------------------------------
        def _step_8() -> dict[str, Any]:
            from src.db.data_service import load_indicator_snapshot

            # load_indicator_snapshot automatically checks if cache is stale:
            # (db_max_date changed, adj_factors count changed, schema version bumped, etc.)
            logger.info("Checking indicator cache freshness / rebuilding if stale...")
            snapshot, _full, is_live, msg = load_indicator_snapshot(force_refresh=False)
            logger.info(
                "Indicator cache check complete: %d snapshot rows (%s).",
                len(snapshot),
                msg,
            )
            return {"snapshot_len": len(snapshot), "is_live": is_live, "message": msg}

        _run_step(8, _step_8)

        # ---------------------------------------------------------------------
        # Step 9: print one summary
        # ---------------------------------------------------------------------
        def _step_9() -> DailyUpdateSummary:
            summary_text = summary.format_text()
            print(summary_text)  # noqa: T201
            logger.info("Daily update completed successfully.")
            return summary

        _run_step(9, _step_9)

        return summary

    if session is not None:
        return _execute(session)
    with get_db() as managed_session:
        return _execute(managed_session)
