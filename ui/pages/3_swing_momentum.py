"""
Strategy: Swing With Momentum
==============================
Filters for institutional breakout equities exhibiting multi-quarter structural
accumulation, strong medium-term trend velocity, and disciplined non-overheated price expansion.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("ui.swing_momentum")

from typing import Any
import streamlit as st
import pandas as pd
import numpy as np

from src.db.data_service import (
    load_raw_market_data,
    compute_market_indicators,
    get_latest_market_snapshot,
)
from src.screener import StockScreener
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.components.stock_inspector import render_stock_inspector


def setup_page_config() -> None:
    st.set_page_config(
        page_title="Swing With Momentum Strategy | ApexGrowth",
        page_icon="🚀",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def get_market_data() -> dict[str, Any]:
    if "market_data_cache" not in st.session_state or st.session_state.market_data_cache is None:
        raw_df, is_live, status_msg = load_raw_market_data()
        with_indicators = compute_market_indicators(raw_df)
        snapshot = get_latest_market_snapshot(with_indicators)
        st.session_state.market_data_cache = {
            "raw": raw_df,
            "indicators": with_indicators,
            "snapshot": snapshot,
            "is_live": is_live,
            "status_msg": status_msg,
        }
    return st.session_state.market_data_cache


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()

        data = get_market_data()
        snapshot = data["snapshot"]
        indicators_df = data["indicators"]
        is_live = data["is_live"]

        render_terminal_header(
            title="Swing With Momentum",
            subtitle="INSTITUTIONAL MOMENTUM & MULTI-HORIZON ACCUMULATION ALPHA",
            status_text="LIVE MYSQL" if is_live else "DEMO MODE",
            is_live=is_live,
        )

        # Compact Filter Chips Ribbon (Zero Verbose Essay)
        st.markdown("""
        <div class="filter-ribbon">
            <span class="filter-chip chip-cyan">⚡ Baseline: 100% Swing + Volume Qualified</span>
            <span class="filter-chip chip-amber">🛡️ Anti-Climax: 60D Return &le; +40%</span>
            <span class="filter-chip chip-purple">📐 Base Spread: (C - C252)/C180 &le; 300%</span>
            <span class="filter-chip chip-purple">📏 Horizon Ceiling: (C - C180)/C252 &le; 300%</span>
            <span class="filter-chip chip-green">🚀 Horizon Floor: (C - C180)/C252 &ge; +50%</span>
            <span class="filter-chip chip-cyan">🔥 Velocity: 120D Return &ge; +30%</span>
        </div>
        """, unsafe_allow_html=True)

        if snapshot.empty:
            st.info("Market data is empty. Run the ETL pipeline to ingest data.")
            return

        screener = StockScreener(snapshot)
        qualifying = screener.strategy_swing_momentum()

        # Telemetry KPIs
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Momentum Alpha Equities", f"{len(qualifying)}")
        with kpi2:
            median_120d = qualifying['return_120d'].median() if ('return_120d' in qualifying and not qualifying.empty) else 0.0
            st.metric("Median 120D Return", f"{median_120d:+.1f}%")
        with kpi3:
            median_60d = qualifying['return_60d'].median() if ('return_60d' in qualifying and not qualifying.empty) else 0.0
            st.metric("Median 60D Return", f"{median_60d:+.1f}%")
        with kpi4:
            avg_rsi = qualifying['rsi_14'].mean() if not qualifying.empty else 0.0
            st.metric("Average RSI (14)", f"{avg_rsi:.1f}")

        st.markdown("---")

        if qualifying.empty:
            st.info("No stocks currently satisfy all criteria for Swing With Momentum.")
            return

        # Prepare Clean Display Table
        qualifying = qualifying.copy()
        c_180 = qualifying['close_180d'].replace(0, np.nan)
        c_252 = qualifying['close_252d'].replace(0, np.nan)
        vol_denom = qualifying['volume_sma_20'].replace(0, np.nan)
        qualifying['ratio_252_180'] = ((qualifying['close'] - qualifying['close_252d']) / c_180) * 100.0
        qualifying['ratio_180_252'] = ((qualifying['close'] - qualifying['close_180d']) / c_252) * 100.0
        qualifying['vol_multiple'] = qualifying['volume'] / vol_denom

        cols_to_show = [
            'symbol', 'index_name', 'close', 'pct_change_daily',
            'return_60d', 'return_120d',
            'ratio_180_252', 'ratio_252_180',
            'volume', 'vol_multiple',
            'close_60d', 'close_120d', 'close_180d', 'close_252d',
            'rsi_14'
        ]
        available_cols = [c for c in cols_to_show if c in qualifying.columns]
        display_df = qualifying[available_cols].copy()

        name_map = {
            'symbol': 'Symbol',
            'index_name': 'Index',
            'close': 'Close',
            'pct_change_daily': '% Change Daily',
            'return_60d': '60D Return',
            'return_120d': '120D Return',
            'ratio_180_252': '(C - C180)/C252 %',
            'ratio_252_180': '(C - C252)/C180 %',
            'volume': 'Volume',
            'vol_multiple': 'Vol Multiple',
            'close_60d': 'Close 60D Ago',
            'close_120d': 'Close 120D Ago',
            'close_180d': 'Close 180D Ago',
            'close_252d': 'Close 252D Ago',
            'rsi_14': 'RSI (14)',
        }
        display_df = display_df.rename(columns=name_map)

        st.dataframe(
            display_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Close": st.column_config.NumberColumn(format="₹%.2f"),
                "% Change Daily": st.column_config.NumberColumn(format="%.2f%%"),
                "60D Return": st.column_config.NumberColumn(format="%.1f%%"),
                "120D Return": st.column_config.NumberColumn(format="%.1f%%"),
                "(C - C180)/C252 %": st.column_config.NumberColumn(format="%.1f%%"),
                "(C - C252)/C180 %": st.column_config.NumberColumn(format="%.1f%%"),
                "Volume": st.column_config.NumberColumn(format="%d"),
                "Vol Multiple": st.column_config.NumberColumn(format="%.2fx"),
                "Close 60D Ago": st.column_config.NumberColumn(format="₹%.2f"),
                "Close 120D Ago": st.column_config.NumberColumn(format="₹%.2f"),
                "Close 180D Ago": st.column_config.NumberColumn(format="₹%.2f"),
                "Close 252D Ago": st.column_config.NumberColumn(format="₹%.2f"),
                "RSI (14)": st.column_config.NumberColumn(format="%.2f"),
            }
        )

        col_dl, col_tv = st.columns([1, 1])
        with col_dl:
            st.download_button(
                label="📥 Download Results (CSV)",
                data=display_df.to_csv(index=False),
                file_name="nse_swing_with_momentum.csv",
                mime="text/csv",
                width="stretch",
            )
        with col_tv:
            tv_symbols = ",".join([f"NSE:{s}" for s in qualifying['symbol'].unique()])
            st.text_input("TradingView Watchlist String (Copy & Paste into TV):", value=tv_symbols)

        st.markdown("---")

        # Interactive Technical Stock Inspector Drilldown
        top_symbol = qualifying.iloc[0]["symbol"] if not qualifying.empty else None
        render_stock_inspector(indicators_df, default_symbol=top_symbol, key_prefix="swing_mom")

    except Exception as exc:
        logger.error("Swing With Momentum page error: %s", exc, exc_info=True)
        st.error(f"Strategy error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
