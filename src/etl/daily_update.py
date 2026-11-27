"""
Daily routine: ``python run_pipeline.py --update``
==================================================
``run_daily_update()`` runs seven steps in strict order and stops at the first failure.
Each step is one plain function ``step_xxx(session, ctx)`` further down this file.

Steps in order:
 1. load_prices: from the day after the last loaded date up to TODAY (old gaps first)
 2. market_cap: for the new dates
 3. refresh_reference_data: EQUITY_L, symbol changes and corporate actions
 4. check_bogus_sessions: only the dates loaded in this run; report only, never deletes
 5. refresh_benchmarks: index closes needed by the relative-strength indicators
 6. adjust_prices: for new rows (and any row an earlier failed run left unadjusted)
 7. rebuild_indicator_cache: rebuilds itself when the database changed

Then one summary is printed. Each step commits its own work when it succeeds; a failed
step rolls back only itself, so the committed work of earlier steps stays in the database.

Trying today is safe: a 404 for a day within the last 3 days is never cached as a
holiday (see nse-data-integrity), so an early run just picks the file up the next time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import FIRST_MCAP_DATE, NSE_HISTORY_START
from src.common.db import use_session
from src.common.trading_calendar import get_trading_dates, latest_date
from src.db.models import CorporateAction, DailyPrice, Security, SymbolChange

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


@dataclass
class UpdateContext:
    """What every step needs besides the database session."""

    today: date
    summary: DailyUpdateSummary = field(default_factory=DailyUpdateSummary)


# ---------------------------------------------------------------------------
# The steps. Each is (session, ctx) -> dict; the dict is stored in summary.step_results.
# ---------------------------------------------------------------------------
def step_load_prices(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Retry old gap days, then load every new day up to today, then report what is missing."""
    from src.ingestion.price_loader import (
        find_price_gaps,
        load_raw_prices,
        write_price_gaps_report,
    )

    # Old holes first: the new-day start is max(date)+1, so a failed middle day would
    # never be visited again otherwise.
    for gap_day in find_price_gaps(s):
        logger.info("Retrying price gap %s...", gap_day)
        load_raw_prices(start=gap_day, end=gap_day, session=s, ref_date=ctx.today)

    result = {"new_dates": [], "rows_added": 0}
    max_date = latest_date(s)
    start_date = max_date + timedelta(days=1) if max_date else NSE_HISTORY_START
    if start_date > ctx.today:
        logger.info("Latest date in DB (%s) is already up to date with today.", max_date)
    else:
        logger.info("Loading raw Bhavcopy prices from %s to %s...", start_date, ctx.today)
        load_summary = load_raw_prices(
            start=start_date,
            end=ctx.today,
            session=s,
            ref_date=ctx.today,
        )
        ctx.summary.new_dates = get_trading_dates(s, start_date, ctx.today)
        ctx.summary.rows_added = load_summary.rows_inserted
        if load_summary.bad_file > 0:
            ctx.summary.warnings.append(
                f"{load_summary.bad_file} corrupt or empty Bhavcopy files encountered."
            )
        result = {"new_dates": ctx.summary.new_dates, "rows_added": load_summary.rows_inserted}

    ctx.summary.price_gaps = find_price_gaps(s)
    write_price_gaps_report(ctx.summary.price_gaps)
    if ctx.summary.price_gaps:
        ctx.summary.warnings.append(
            f"{len(ctx.summary.price_gaps)} calendar days have no prices and are not "
            "confirmed holidays (reports/price_gaps.csv)."
        )
    return result


def step_market_cap(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Fill market cap for the new dates (or for recent dates that still lack it)."""
    from src.ingestion.mcap_loader import load_market_caps

    target_dates = [d for d in ctx.summary.new_dates if d >= FIRST_MCAP_DATE]
    if not target_dates:
        # No new dates: still repair recent trading dates that have no market cap.
        target_dates = list(
            s.scalars(
                select(DailyPrice.date)
                .distinct()
                .where(
                    DailyPrice.date >= max(FIRST_MCAP_DATE, ctx.today - timedelta(days=7)),
                    DailyPrice.date <= ctx.today,
                    DailyPrice.market_cap.is_(None),
                )
                .order_by(DailyPrice.date)
            ).all()
        )
    if not target_dates:
        logger.info("No new dates requiring market cap load.")
        return {"updated_rows": 0, "unmatched": 0}

    logger.info(
        "Loading market cap from PR bundles for dates %s to %s...",
        min(target_dates),
        max(target_dates),
    )
    mcap = load_market_caps(start=min(target_dates), end=max(target_dates), session=s)
    ctx.summary.mcap_rows_updated = mcap.updated_rows
    if mcap.unmatched_symbols > 0:
        ctx.summary.warnings.append(
            f"{mcap.unmatched_symbols} PR bundle market cap symbols were not in daily_prices."
        )
    return {"updated_rows": mcap.updated_rows, "unmatched": mcap.unmatched_symbols}


def _active_symbols(s: Session) -> set[str]:
    return set(s.scalars(select(Security.symbol).where(Security.status == "active")).all())


def _count_review_events(s: Session) -> int:
    """How many stored corporate actions are waiting for the owner's review."""
    return (
        s.scalar(
            select(func.count())
            .select_from(CorporateAction)
            .where(CorporateAction.parse_status == "needs_review")
        )
        or 0
    )


def step_refresh_reference_data(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Refresh the securities list, symbol renames and corporate actions from NSE."""
    from src.ingestion.corporate_actions_store import run_corporate_actions_download
    from src.ingestion.renames import load_renames
    from src.ingestion.securities import import_equity_l

    active_before = _active_symbols(s)
    renames_before = set(s.execute(select(SymbolChange.old_symbol, SymbolChange.new_symbol)).all())
    review_before = _count_review_events(s)

    equity_rows = import_equity_l(session=s)
    rename_rows = load_renames(session=s)
    actions = run_corporate_actions_download(session=s, today=ctx.today)
    # The download may re-parse old rows, so flush before counting what is stored now.
    s.flush()

    # Renames are counted as rows that are new in the table, not by comparing parser counts.
    renames_after = set(s.execute(select(SymbolChange.old_symbol, SymbolChange.new_symbol)).all())
    ctx.summary.renamed_symbols = sorted(renames_after - renames_before)
    ctx.summary.new_symbols = sorted(_active_symbols(s) - active_before)
    ctx.summary.dropped_symbols = sorted(
        s.scalars(select(Security.symbol).where(Security.status != "active")).all()
    )

    # Warn only when the review list grew in this run: the total covers ten years and
    # would otherwise raise the same warning every day.
    new_reviews = _count_review_events(s) - review_before
    if new_reviews > 0:
        ctx.summary.warnings.append(
            f"{new_reviews} new corporate action event(s) need owner review in "
            "reports/adj_events_uncertain.csv."
        )
    logger.info(
        "Refreshed securities (%d rows), renames (%d rows) and corporate actions (%d stored). "
        "New active: %d, dropped: %d, new renames: %d, new reviews: %d.",
        equity_rows,
        rename_rows,
        actions.get("rows_stored", 0),
        len(ctx.summary.new_symbols),
        len(ctx.summary.dropped_symbols),
        len(ctx.summary.renamed_symbols),
        max(new_reviews, 0),
    )
    return {
        "equity_l_rows": equity_rows,
        "renames_rows": rename_rows,
        "new_active": len(ctx.summary.new_symbols),
        **actions,
    }


def step_check_bogus_sessions(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Report (never delete) new dates that look like a copy of the previous session."""
    from src.etl.bogus_sessions import report_bogus_sessions

    result = report_bogus_sessions(session=s, only_dates=ctx.summary.new_dates)
    ctx.summary.bogus_sessions_flagged = result["bogus_count"]
    if result["bogus_count"] > 0:
        message = (
            f"{result['bogus_count']} bogus duplicate/holiday session(s) detected: "
            f"{[str(d) for d in result['flagged_dates']]}. Report only; no rows were deleted."
        )
        logger.warning(message)
        ctx.summary.warnings.append(message)
    return result


def step_refresh_benchmarks(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Load index closes for every price date that has none (gaps in the middle too)."""
    from src.db.models import BenchmarkPrice
    from src.ingestion.benchmark import load_benchmarks, missing_benchmark_dates

    last_bench = latest_date(s, BenchmarkPrice)
    if last_bench is None:
        message = (
            "No benchmark history stored; run "
            "`python run_pipeline.py --benchmarks --from 2016-11-21` once."
        )
        logger.warning(message)
        ctx.summary.warnings.append(message)
        return {"saved_rows": 0}

    # Dates NSE has no file for are remembered as non-trading and skipped.
    new_dates = [d for d in missing_benchmark_dates(s) if d <= ctx.today]
    if not new_dates:
        logger.info("Benchmarks have no missing dates (last %s).", last_bench)
        return {"saved_rows": 0}

    saved = load_benchmarks(new_dates, session=s)
    ctx.summary.benchmark_rows_saved = saved
    return {"saved_rows": saved}


def step_adjust_prices(s: Session, ctx: UpdateContext) -> dict[str, Any]:
    """Adjust the new price rows, and repair rows an earlier failed run left unadjusted."""
    from src.etl.adjust import adjust_new_rows, adjust_rows_with_missing_adjusted

    updated = 0
    if ctx.summary.new_dates:
        new_rows = s.scalars(
            select(DailyPrice).where(DailyPrice.date.in_(ctx.summary.new_dates))
        ).all()
        if new_rows:
            frame = pd.DataFrame(
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
                    for r in new_rows
                ]
            )
            updated = adjust_new_rows(frame, session=s)
            logger.info("Adjusted new price rows: %d rows updated.", updated)
    else:
        logger.info("No new price dates loaded.")

    updated += adjust_rows_with_missing_adjusted(s)
    ctx.summary.adjusted_rows = updated
    return {"updated_rows": updated}


# Every step takes (session, ctx) so the loop can call them alike; this one needs neither.
def step_rebuild_indicator_cache(s: Session, ctx: UpdateContext) -> dict[str, Any]:  # noqa: ARG001
    """Rebuild the indicator cache if it is stale (new date, new factors, new schema)."""
    from src.db.data_service import load_indicator_snapshot

    logger.info("Checking indicator cache freshness / rebuilding if stale...")
    snapshot, _full, is_live, message = load_indicator_snapshot(force_refresh=False)
    logger.info("Indicator cache check complete: %d snapshot rows (%s).", len(snapshot), message)
    return {"snapshot_len": len(snapshot), "is_live": is_live, "message": message}


def run_daily_update(
    session: Session | None = None,
    today: date | None = None,
) -> DailyUpdateSummary:
    """Run the seven steps in order; stop at the first failure with a DailyUpdateError.

    ``session`` is optional (a session is opened when missing); ``today`` is the last day
    to try (default: date.today()). Tests replace ``nse_files.fetch`` to stay offline.
    """
    ctx = UpdateContext(today=today or date.today())
    # Looked up here, not at import, so a test can replace one step function.
    steps = [
        ("load_prices", step_load_prices),
        ("market_cap", step_market_cap),
        ("refresh_reference_data", step_refresh_reference_data),
        ("check_bogus_sessions", step_check_bogus_sessions),
        ("refresh_benchmarks", step_refresh_benchmarks),
        ("adjust_prices", step_adjust_prices),
        ("rebuild_indicator_cache", step_rebuild_indicator_cache),
    ]
    with use_session(session) as s:
        for number, (name, step) in enumerate(steps, start=1):
            logger.info("Starting Daily Update Step %d: %s...", number, name)
            try:
                ctx.summary.step_results[name] = step(s, ctx)
                # KEEP: commit per step. The cache step reads through its own connection; under
                # MySQL REPEATABLE READ it would not see uncommitted writes and could build the
                # indicator cache from OLD data.
                s.commit()
            except Exception as exc:
                # A failed step undoes only its own half-written work; earlier steps stay saved.
                s.rollback()
                logger.error("Failed Daily Update Step %d (%s): %s", number, name, exc)
                raise DailyUpdateError(number, name, exc) from exc

    print(ctx.summary.format_text())  # noqa: T201
    logger.info("Daily update completed successfully.")
    return ctx.summary
