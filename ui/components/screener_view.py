"""
Reusable Screener View Component
================================
Renders a standardized UI for any registered Screener: sidebar settings (market cap
filter and indicator editor), KPI tiles, one result tab per NSE series, near-miss
diagnostics, CSV exports and a stock inspector limited to the stocks shown.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from src.screeners.registry import get_screener
from ui.components.dashboard_helpers import SERIES_LABELS
from ui.components.screener_helpers import (
    apply_market_cap_filter,
    build_near_miss_table,
    build_results_table,
    group_by_series,
    inspector_symbols,
)
from ui.components.screener_sidebar import render_indicator_settings, render_market_cap_filter
from ui.components.stock_inspector import render_stock_inspector
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.data import get_snapshot, render_uninitialized_prompt

logger = logging.getLogger("ui.screener_view")

COLUMN_CONFIG = {
    "market_cap_cr": st.column_config.NumberColumn("Market Cap (₹ Cr)", format="₹%.0f Cr"),
    "close": st.column_config.NumberColumn("Close (₹)", format="₹%.2f"),
    "pct_change_daily": st.column_config.NumberColumn("% Change", format="%.2f%%"),
    "volume": st.column_config.NumberColumn("Volume", format="%d"),
    "vol_multiple": st.column_config.NumberColumn("Vol ×20D", format="%.2fx"),
    "ema_20": st.column_config.NumberColumn("EMA 20", format="₹%.2f"),
    "sma_50": st.column_config.NumberColumn("SMA 50", format="₹%.2f"),
    "sma_200": st.column_config.NumberColumn("SMA 200", format="₹%.2f"),
    "rsi_14": st.column_config.NumberColumn("RSI (14)", format="%.1f"),
    "symbol": st.column_config.TextColumn("Symbol"),
    "series": st.column_config.TextColumn("Series"),
    "failed_condition": st.column_config.TextColumn("Failed condition (bottleneck)"),
}


def ribbon_html(chips: list[tuple[str, str]]) -> str:
    """HTML for a row of condition chips; each chip is ``(css_color_name, text)``."""
    spans = "".join(f'<span class="filter-chip chip-{c}">{t}</span>' for c, t in chips)
    return f'<div class="filter-ribbon">{spans}</div>'


def setup_page(page_title: str, icon: str = "⚡") -> None:
    """Wide layout and the terminal theme (set_page_config may only run once per session)."""
    with contextlib.suppress(Exception):
        st.set_page_config(
            page_title=f"{page_title} | Trading Terminal",
            page_icon=icon,
            layout="wide",
            initial_sidebar_state="expanded",
        )
    apply_terminal_theme()


def render_screener_page(
    screener_key: str,
    page_title: str,
    subtitle: str,
    ribbon_builder: Callable[[dict[str, Any]], str] | None = None,
    key_prefix: str = "screener",
    csv_filename: str | None = None,
) -> None:
    """Render a complete screener page for one fixed screener (Swing + Volume, ...)."""
    setup_page(page_title)

    data = get_snapshot()
    snapshot = data["snapshot"]

    render_terminal_header(title=page_title, subtitle=subtitle, as_of=data["as_of"])
    st.caption("ℹ️ *Prices are split/bonus adjusted.*")

    if snapshot.empty:
        render_uninitialized_prompt(data["message"])
        return

    st.sidebar.markdown("### ⚙️ Screener settings")
    min_cr = render_market_cap_filter(key_prefix)
    overrides = render_indicator_settings(type(get_screener(screener_key)), key_prefix)
    screener = get_screener(screener_key, **overrides)

    if ribbon_builder:
        st.markdown(ribbon_builder(screener.params), unsafe_allow_html=True)

    stem = (csv_filename or screener_key).removesuffix(".csv")
    render_results(snapshot, screener, min_cr, stem, key_prefix)


def render_results(
    snapshot: pd.DataFrame,
    screener: Any,
    min_market_cap_cr: float,
    csv_stem: str,
    key_prefix: str,
) -> None:
    """Filter, run the screener and draw KPIs, series tabs, diagnostics and the inspector."""
    universe = apply_market_cap_filter(snapshot, min_market_cap_cr)
    hidden = len(snapshot) - len(universe)
    if hidden:
        st.caption(f"{hidden:,} EQ stocks below ₹{min_market_cap_cr:,.0f} Cr are hidden.")

    explained = screener.explain(universe)
    qualifying = explained[explained["passed"]]
    near_misses = explained[explained["near_miss"]]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Qualifying", f"{len(qualifying):,}", help="Stocks passing every condition.")
    k2.metric(
        "Near-miss",
        f"{len(near_misses):,}",
        help="Stocks failing exactly one condition (the bottleneck).",
    )
    hit_rate = len(qualifying) / len(universe) * 100.0 if len(universe) else 0.0
    k3.metric("Hit rate", f"{hit_rate:.1f}%", help=f"Of {len(universe):,} stocks screened.")
    has_rsi = not qualifying.empty and "rsi_14" in qualifying.columns
    k4.metric("Avg RSI (14)", f"{qualifying['rsi_14'].mean():.1f}" if has_rsi else "—")

    st.divider()
    _render_series_tabs(explained, csv_stem, key_prefix)

    with st.expander("📋 Diagnostic matrix (every stock, condition by condition)"):
        cond_cols = [c for c in explained.columns if c.startswith(("cond_", "pass_"))]
        wanted = ["symbol", "series", "close", "conditions_passed", "conditions_total", "passed"]
        shown = [c for c in [*wanted, "near_miss", *cond_cols] if c in explained.columns]
        st.dataframe(explained[shown], width="stretch", hide_index=True)

    st.divider()
    symbols = inspector_symbols(explained)
    render_stock_inspector(
        explained,
        default_symbol=symbols[0] if symbols else None,
        key_prefix=key_prefix,
        symbols=symbols,
    )


def _render_series_tabs(explained: pd.DataFrame, csv_stem: str, key_prefix: str) -> None:
    """One tab per NSE series with that series' passing stocks (near-misses on a toggle)."""
    groups = group_by_series(explained)
    if not groups:
        st.info("No stocks to screen with the current filters.")
        return

    labels = [f"{name} · {int(group['passed'].sum())}" for name, group in groups]
    for tab, (name, group) in zip(st.tabs(labels), groups, strict=True):
        with tab:
            passing = group[group["passed"]]
            near = group[group["near_miss"]]
            st.caption(
                f"{SERIES_LABELS.get(name, name)}: {len(passing):,} passing "
                f"of {len(group):,} stocks screened"
            )
            if passing.empty:
                st.info("No stocks in this series satisfy all conditions.")
            else:
                table = build_results_table(passing)
                _show_table(table)
                st.download_button(
                    "📥 Download (CSV)",
                    data=table.to_csv(index=False),
                    file_name=f"{csv_stem}_{name}.csv",
                    mime="text/csv",
                    key=f"{key_prefix}_dl_{name}",
                )
            if not near.empty and st.toggle(
                f"Show near-miss candidates ({len(near):,})", key=f"{key_prefix}_near_{name}"
            ):
                st.caption("Stocks that passed everything except one condition:")
                _show_table(build_near_miss_table(near))


def _show_table(table: pd.DataFrame) -> None:
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={k: v for k, v in COLUMN_CONFIG.items() if k in table.columns},
    )
