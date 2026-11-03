"""
Screener Explorer & Diagnostic Terminal
========================================
Interactive explorer for all registered systematic screeners across categories
(Trend, Breakout, Pullback, Swing, Classic, Quantitative) featuring:
- Dynamic parameter adjustments
- Instant condition-level explainability
- Near-miss candidate detection (stocks failing by exactly 1 condition)
- Stock Inspector chart drilldowns
"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging

setup_logging()
logger = logging.getLogger("ui.screener_explorer")

import streamlit as st

from src.screeners.registry import get_screener, list_screeners
from ui.components.stock_inspector import render_stock_inspector
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.data import get_market_data, render_uninitialized_prompt


def setup_page_config() -> None:
    with contextlib.suppress(Exception):
        st.set_page_config(
            page_title="Screener Explorer | Trading Terminal",
            page_icon="🔬",
            layout="wide",
            initial_sidebar_state="expanded",
        )


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()

        data = get_market_data()
        snapshot = data["snapshot"]
        indicators_df = data["indicators"]
        is_live = data["is_live"]

        render_terminal_header(
            title="Screener Explorer",
            subtitle="SYSTEMATIC SCREENING PLATFORM & NEAR-MISS DIAGNOSTICS",
            status_text="LIVE MYSQL" if is_live else "DATABASE OFFLINE",
            is_live=is_live,
        )

        st.caption("ℹ️ *Prices are split/bonus adjusted.*")

        if snapshot.empty:
            render_uninitialized_prompt()
            return

        all_screeners = list_screeners()
        if not all_screeners:
            st.warning("No screeners registered in the system.")
            return

        # Sidebar Screener Selection
        st.sidebar.markdown("### 🔍 Select Screener")
        categories = sorted({s.category.upper() for s in all_screeners})
        selected_cat = st.sidebar.selectbox("Category Filter", ["ALL", *categories])

        filtered_screeners = (
            all_screeners
            if selected_cat == "ALL"
            else [s for s in all_screeners if s.category.upper() == selected_cat]
        )

        screener_labels = {f"[{s.category.upper()}] {s.name}": s.key for s in filtered_screeners}
        selected_label = st.sidebar.selectbox("Screener", list(screener_labels.keys()))
        selected_key = screener_labels[selected_label]

        screener_cls = next(s for s in all_screeners if s.key == selected_key)

        # Screener Overview Card
        st.markdown(
            f"""
        <div style="background: rgba(30, 41, 59, 0.5); padding: 12px 18px; border-radius: 8px;
                    border-left: 4px solid #38bdf8; margin-bottom: 16px;">
            <div style="display: flex; gap: 10px; align-items: center;">
                <span style="background: #0284c7; color: #fff; font-size: 11px; padding: 2px 8px;
                             border-radius: 4px; font-weight: 600;">
                    {screener_cls.category.upper()}
                </span>
                <span style="color: #94a3b8; font-size: 12px;">
                    Version {screener_cls.version} &bull; Key: <code>{screener_cls.key}</code>
                </span>
            </div>
            <div style="color: #e2e8f0; font-size: 14px; margin-top: 6px;">
                {screener_cls.description}
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        # Instantiate screener
        screener = get_screener(selected_key)
        explained = screener.explain(snapshot)

        qualifying = explained[explained["passed"]].copy()
        near_misses = explained[explained["near_miss"]].copy()

        # Telemetry KPIs
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.metric("Qualifying Equities", f"{len(qualifying)}")
        with k2:
            st.metric("Near-Miss Candidates (1 Miss)", f"{len(near_misses)}")
        with k3:
            hit_rate = (len(qualifying) / len(snapshot) * 100.0) if len(snapshot) > 0 else 0.0
            st.metric("Hit Rate", f"{hit_rate:.1f}%")
        with k4:
            has_rsi = not qualifying.empty and "rsi_14" in qualifying.columns
            avg_rsi = qualifying["rsi_14"].mean() if has_rsi else 0.0
            st.metric("Average RSI (14)", f"{avg_rsi:.1f}")

        st.markdown("---")

        tab_pass, tab_near, tab_matrix = st.tabs(
            ["🎯 Passing Equities", "🔍 Near-Miss Candidates", "📋 Diagnostic Matrix"]
        )

        with tab_pass:
            if qualifying.empty:
                st.info(f"No equities currently satisfy all conditions for {screener.name}.")
            else:
                cols_order = [
                    "symbol",
                    "index_name",
                    "close",
                    "pct_change_daily",
                    "volume",
                    "volume_sma_20",
                    "ema_20",
                    "sma_50",
                    "sma_200",
                    "rsi_14",
                ]
                avail = [c for c in cols_order if c in qualifying.columns]
                st.dataframe(qualifying[avail], width="stretch", hide_index=True)
                st.download_button(
                    label=f"📥 Download {screener.name} Equities (CSV)",
                    data=qualifying[avail].to_csv(index=False),
                    file_name=f"{selected_key}_results.csv",
                    mime="text/csv",
                    width="stretch",
                )

        with tab_near:
            if near_misses.empty:
                st.info("No stocks currently failing by exactly 1 condition.")
            else:
                st.caption("Equities passing all criteria except a single bottleneck condition:")
                nm_cols = [
                    "symbol",
                    "close",
                    "near_miss_on",
                    "conditions_passed",
                    "conditions_total",
                ]
                avail_nm = [c for c in nm_cols if c in near_misses.columns]
                nm_display = near_misses[avail_nm].rename(
                    columns={"near_miss_on": "Failed Condition (Bottleneck)"}
                )
                st.dataframe(nm_display, width="stretch", hide_index=True)

        with tab_matrix:
            st.caption("Condition-level breakdown across the universe:")
            cond_cols = [
                c for c in explained.columns if c.startswith("cond_") or c.startswith("pass_")
            ]
            matrix_cols = [
                "symbol",
                "close",
                "conditions_passed",
                "conditions_total",
                "passed",
                "near_miss",
                *cond_cols,
            ]
            avail_matrix = [c for c in matrix_cols if c in explained.columns]
            st.dataframe(explained[avail_matrix], width="stretch", hide_index=True)

        st.markdown("---")

        top_symbol = (
            qualifying.iloc[0]["symbol"]
            if not qualifying.empty
            else (near_misses.iloc[0]["symbol"] if not near_misses.empty else None)
        )
        render_stock_inspector(indicators_df, default_symbol=top_symbol, key_prefix="explorer")

    except Exception as exc:
        logger.error("Screener Explorer page error: %s", exc, exc_info=True)
        st.error(f"Explorer error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
