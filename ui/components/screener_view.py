"""
Reusable Screener View Component
================================
Renders a standardized, high-performance UI view for any registered Screener,
including condition-by-condition explainability, near-miss diagnostics,
and CSV exports.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from src.screeners.registry import get_screener
from ui.components.stock_inspector import render_stock_inspector
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.data import get_market_data, render_uninitialized_prompt

logger = logging.getLogger("ui.screener_view")


def render_screener_page(
    screener_key: str,
    page_title: str,
    subtitle: str,
    ribbon_html: str = "",
    key_prefix: str = "screener",
    csv_filename: str | None = None,
    screener_params: dict[str, Any] | None = None,
) -> None:
    """Render a comprehensive screener page."""
    with contextlib.suppress(Exception):
        st.set_page_config(
            page_title=f"{page_title} | Trading Terminal",
            page_icon="⚡",
            layout="wide",
            initial_sidebar_state="expanded",
        )

    apply_terminal_theme()

    data = get_market_data()
    snapshot = data["snapshot"]
    indicators_df = data["indicators"]
    is_live = data["is_live"]

    render_terminal_header(
        title=page_title,
        subtitle=subtitle,
        status_text="LIVE MYSQL" if is_live else "DATABASE OFFLINE",
        is_live=is_live,
    )

    st.caption("ℹ️ *Prices are split/bonus adjusted.*")

    if snapshot.empty:
        render_uninitialized_prompt()
        return

    if ribbon_html:
        st.markdown(ribbon_html, unsafe_allow_html=True)

    screener = get_screener(screener_key, **(screener_params or {}))
    explained = screener.explain(snapshot)

    qualifying = explained[explained["passed"]].copy()
    near_misses = explained[explained["near_miss"]].copy()

    # KPI Telemetry Banner
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.metric("Qualifying Equities", f"{len(qualifying)}")
    with kpi2:
        st.metric("Near-Miss Equities (1 Miss)", f"{len(near_misses)}")
    with kpi3:
        hit_rate = (len(qualifying) / len(snapshot) * 100.0) if len(snapshot) > 0 else 0.0
        st.metric("Hit Rate", f"{hit_rate:.1f}%")
    with kpi4:
        has_rsi = not qualifying.empty and "rsi_14" in qualifying.columns
        avg_rsi = qualifying["rsi_14"].mean() if has_rsi else 0.0
        st.metric("Average RSI (14)", f"{avg_rsi:.1f}")

    st.markdown("---")

    tab_pass, tab_near, tab_all = st.tabs(
        ["🎯 Passing Equities", "🔍 Near-Miss Candidates", "📋 Full Universe Status"]
    )

    with tab_pass:
        if qualifying.empty:
            st.info(f"No stocks currently satisfy all conditions for {screener.name}.")
        else:
            _render_results_table(qualifying, csv_filename or f"{screener_key}.csv")

    with tab_near:
        if near_misses.empty:
            st.info("No near-miss stocks (exactly 1 failed condition) found.")
        else:
            st.caption("Stocks that passed all screening criteria except for a single condition:")
            _render_near_miss_table(near_misses)

    with tab_all:
        st.caption("Condition-level breakdown across the entire universe:")
        _render_explanation_table(explained)

    st.markdown("---")

    # Interactive Technical Stock Inspector
    top_symbol = (
        qualifying.iloc[0]["symbol"]
        if not qualifying.empty
        else (near_misses.iloc[0]["symbol"] if not near_misses.empty else None)
    )
    render_stock_inspector(indicators_df, default_symbol=top_symbol, key_prefix=key_prefix)


def _render_results_table(df: pd.DataFrame, csv_filename: str) -> None:
    res = df.copy()
    if "volume" in res.columns and "volume_sma_20" in res.columns:
        v_nz = res["volume_sma_20"].replace(0, np.nan)
        res["vol_multiple"] = res["volume"] / v_nz

    if "high_252d_prev" in res.columns and "close" in res.columns:
        h_nz = res["high_252d_prev"].replace(0, np.nan)
        res["pct_from_52w_high"] = ((res["close"] - res["high_252d_prev"]) / h_nz) * 100.0

    cols_order = [
        "symbol",
        "index_name",
        "close",
        "pct_change_daily",
        "volume",
        "volume_sma_20",
        "vol_multiple",
        "ema_20",
        "sma_50",
        "sma_200",
        "rsi_14",
    ]
    available = [c for c in cols_order if c in res.columns]
    display_df = res[available].copy()

    st.dataframe(display_df, width="stretch", hide_index=True)
    st.download_button(
        label="📥 Download Passing Equities (CSV)",
        data=display_df.to_csv(index=False),
        file_name=csv_filename,
        mime="text/csv",
        width="stretch",
    )


def _render_near_miss_table(df: pd.DataFrame) -> None:
    cols = ["symbol", "close", "near_miss_on", "conditions_passed", "conditions_total"]
    available = [c for c in cols if c in df.columns]
    display_df = df[available].copy()
    display_df = display_df.rename(columns={"near_miss_on": "Failed Condition (Bottleneck)"})
    st.dataframe(display_df, width="stretch", hide_index=True)


def _render_explanation_table(df: pd.DataFrame) -> None:
    cond_cols = [c for c in df.columns if c.startswith("cond_") or c.startswith("pass_")]
    cols = [
        "symbol",
        "close",
        "conditions_passed",
        "conditions_total",
        "passed",
        "near_miss",
        *cond_cols,
    ]
    available = [c for c in cols if c in df.columns]
    st.dataframe(df[available], width="stretch", hide_index=True)
