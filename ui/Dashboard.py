"""
Master Stock Screener Dashboard
===============================
Displays all tracked NSE equities with comprehensive quantitative indicators,
dynamic column customization and real-time filtering. Read-only: it never loads data.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging

setup_logging()
logger = logging.getLogger("ui.dashboard")

import contextlib

import streamlit as st

from ui.components.dashboard_helpers import (
    COLUMN_PRESETS,
    SERIES_LABELS,
    color_change,
    compute_dashboard_kpis,
    default_series,
    filter_by_series,
    filter_by_symbol,
    rename_for_display,
)
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.data import get_snapshot, render_uninitialized_prompt


def setup_page_config() -> None:
    # Configure high-resolution widescreen layout for trading terminal
    with contextlib.suppress(Exception):
        st.set_page_config(
            page_title="Master Dashboard | NSE Trading Terminal",
            page_icon="📊",
            layout="wide",
            initial_sidebar_state="expanded",
        )


COLUMN_CONFIG = {
    "Close": st.column_config.NumberColumn("Close (₹)", format="₹%.2f"),
    "Volume": st.column_config.NumberColumn("Volume", format="%d"),
    "% Change Daily": st.column_config.NumberColumn("% Change Daily", format="%.2f%%"),
    "% Change 1M": st.column_config.NumberColumn("% Change 1M", format="%.2f%%"),
    "% Change 3M": st.column_config.NumberColumn("% Change 3M", format="%.2f%%"),
    "% Change 6M": st.column_config.NumberColumn("% Change 6M", format="%.2f%%"),
    "% Change 12M": st.column_config.NumberColumn("% Change 12M", format="%.2f%%"),
    "% From 52W High": st.column_config.NumberColumn("% From 52W High", format="%.1f%%"),
    "SMA 50": st.column_config.NumberColumn("SMA 50", format="₹%.2f"),
    "SMA 200": st.column_config.NumberColumn("SMA 200", format="₹%.2f"),
    "EMA 12": st.column_config.NumberColumn("EMA 12", format="₹%.2f"),
    "EMA 20": st.column_config.NumberColumn("EMA 20", format="₹%.2f"),
    "EMA 26": st.column_config.NumberColumn("EMA 26", format="₹%.2f"),
    "RSI": st.column_config.NumberColumn("RSI (14)", format="%.1f"),
}
PERCENT_COLUMNS = [c for c in COLUMN_CONFIG if c.startswith("% Change")]


def render_kpis(kpis: list[tuple[str, str, str]]) -> None:
    """Draw the KPI tiles in rows of four (rows wrap nicely on narrow screens)."""
    for start in range(0, len(kpis), 4):
        for column, (label, value, help_text) in zip(
            st.columns(4), kpis[start : start + 4], strict=False
        ):
            column.metric(label, value, help=help_text)


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()

        data = get_snapshot()
        snapshot = data["snapshot"]

        render_terminal_header(
            title="NSE Screener",
            subtitle="TECHNICAL & QUANTITATIVE SNAPSHOT",
            as_of=data["as_of"],
        )

        if snapshot.empty:
            render_uninitialized_prompt(data["message"])
            return

        as_of = snapshot["date"].max() if "date" in snapshot.columns else "unknown"
        st.caption(f"Data as of **{as_of}** · prices are split/bonus adjusted")

        # Filters: which series (EQ, SME, ...), a symbol search and a column preset.
        # An older cache may lack the series column; then the selector is skipped.
        has_series = "series" in snapshot.columns
        available = sorted(snapshot["series"].dropna().unique().tolist()) if has_series else []
        f1, f2, f3 = st.columns([2.5, 1.5, 2])
        with f1:
            if has_series:
                chosen_series = st.multiselect(
                    "Series",
                    options=available,
                    default=default_series(available),
                    format_func=lambda s: SERIES_LABELS.get(s, s),
                    help="NSE series: EQ is normal equity; SM/ST are SME stocks; BE/BZ are "
                    "restricted.",
                )
            else:
                chosen_series = []
                st.caption("Series information is missing in the cache; showing all rows.")
        with f2:
            search = st.text_input("Search symbol", placeholder="e.g. RELIANCE")
        with f3:
            preset = st.radio("Columns", [*COLUMN_PRESETS, "Custom"], horizontal=True, index=0)

        by_series = filter_by_series(snapshot, chosen_series) if has_series else snapshot
        filtered = filter_by_symbol(by_series, search)
        table = rename_for_display(filtered)

        render_kpis(compute_dashboard_kpis(filtered, total_rows=len(snapshot)))
        st.divider()

        if preset == "Custom":
            chosen_cols = st.multiselect(
                "Columns to display", options=list(table.columns), default=COLUMN_PRESETS["Basic"]
            )
        else:
            chosen_cols = COLUMN_PRESETS[preset]
        shown_cols = [c for c in chosen_cols if c in table.columns] or list(table.columns)
        display_view = table[shown_cols]

        # Green/red text for % changes; number formats come from COLUMN_CONFIG.
        percent_shown = [c for c in PERCENT_COLUMNS if c in display_view.columns]
        styled = display_view.style.map(color_change, subset=percent_shown)
        st.dataframe(
            styled,
            width="stretch",
            hide_index=True,
            column_config={k: v for k, v in COLUMN_CONFIG.items() if k in display_view.columns},
        )

        st.download_button(
            label="📥 Download table (CSV)",
            data=display_view.to_csv(index=False),
            file_name="nse_master_screener_snapshot.csv",
            mime="text/csv",
        )

    except Exception as exc:
        logger.error("Dashboard page crash: %s", exc, exc_info=True)
        st.error(f"Dashboard error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
