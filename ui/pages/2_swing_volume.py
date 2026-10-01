"""
Strategy: Swing + Volume
=========================
Systematic screening for institutional volume expansion and multi-timeframe bullish trend alignment
across NIFTY 100, NIFTY Midcap 150, and NIFTY Smallcap 250 equities.
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
logger = logging.getLogger("ui.swing_volume")

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
        page_title="Swing + Volume Strategy | ApexGrowth",
        page_icon="🌊",
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
            title="Swing + Volume",
            subtitle="INSTITUTIONAL VOLUME EXPANSION & MULTI-TIMEFRAME TREND BREAKOUT",
            status_text="LIVE MYSQL" if is_live else "DEMO MODE",
            is_live=is_live,
        )

        # Compact Filter Chips Ribbon (Zero Verbose Essay)
        st.markdown("""
        <div class="filter-ribbon">
            <span class="filter-chip chip-cyan">🎯 Universe: NIFTY 100 / Midcap 150 / Smallcap 250</span>
            <span class="filter-chip chip-green">📈 Trend: EMA(20) &gt; SMA(50) &gt; SMA(200)</span>
            <span class="filter-chip chip-cyan">💎 Price: Close &gt; SMA(50) &amp; SMA(200)</span>
            <span class="filter-chip chip-amber">⚡ Peak Proximity: Close &ge; 0.85 &times; 252D High</span>
            <span class="filter-chip chip-purple">🌊 Volume Breakout: Vol &gt; 1.5&times; 20D SMA</span>
            <span class="filter-chip chip-rose">💧 Liquidity Floor: 20D SMA Vol &gt; 250K</span>
        </div>
        """, unsafe_allow_html=True)

        if snapshot.empty:
            st.info("Market data is empty. Run the ETL pipeline to ingest data.")
            return

        screener = StockScreener(snapshot)
        if hasattr(screener, "strategy_swing_volume"):
            qualifying = screener.strategy_swing_volume()
        elif hasattr(screener, "strategy_liquid_volume"):
            qualifying = screener.strategy_liquid_volume()
        else:
            qualifying = screener.liquid_1_5x_volume()

        # Telemetry KPIs
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Qualifying Equities", f"{len(qualifying)}")
        with kpi2:
            hit_rate = (len(qualifying) / len(snapshot) * 100.0) if len(snapshot) > 0 else 0.0
            st.metric("Universe Hit Rate", f"{hit_rate:.1f}%")
        with kpi3:
            median_vol = (qualifying['volume'] / qualifying['volume_sma_20']).median() if not qualifying.empty else 0.0
            st.metric("Median Vol Multiple", f"{median_vol:.2f}x")
        with kpi4:
            avg_rsi = qualifying['rsi_14'].mean() if not qualifying.empty else 0.0
            st.metric("Average RSI (14)", f"{avg_rsi:.1f}")

        st.markdown("---")

        if qualifying.empty:
            st.info("No stocks currently satisfy all criteria for Swing + Volume.")
            return

        # Prepare Clean Display Table
        qualifying = qualifying.copy()
        vol_denom = qualifying['volume_sma_20'].replace(0, np.nan)
        high_denom = qualifying['high_252d_prev'].replace(0, np.nan)
        qualifying['vol_multiple'] = qualifying['volume'] / vol_denom
        qualifying['pct_from_52w_high'] = ((qualifying['close'] - qualifying['high_252d_prev']) / high_denom) * 100.0

        cols_to_show = [
            'symbol', 'index_name', 'close', 'pct_change_daily',
            'volume', 'volume_sma_20', 'vol_multiple',
            'ema_20', 'sma_50', 'sma_200',
            'high_252d_prev', 'pct_from_52w_high', 'rsi_14'
        ]
        available_cols = [c for c in cols_to_show if c in qualifying.columns]
        display_df = qualifying[available_cols].copy()

        name_map = {
            'symbol': 'Symbol',
            'index_name': 'Index',
            'close': 'Close',
            'pct_change_daily': '% Change Daily',
            'volume': 'Volume',
            'volume_sma_20': '20D Avg Vol',
            'vol_multiple': 'Vol Multiple',
            'ema_20': 'EMA 20',
            'sma_50': 'SMA 50',
            'sma_200': 'SMA 200',
            'high_252d_prev': '252D High (Shifted)',
            'pct_from_52w_high': '% From 52W High',
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
                "Volume": st.column_config.NumberColumn(format="%d"),
                "20D Avg Vol": st.column_config.NumberColumn(format="%d"),
                "Vol Multiple": st.column_config.NumberColumn(format="%.2fx"),
                "EMA 20": st.column_config.NumberColumn(format="₹%.2f"),
                "SMA 50": st.column_config.NumberColumn(format="₹%.2f"),
                "SMA 200": st.column_config.NumberColumn(format="₹%.2f"),
                "252D High (Shifted)": st.column_config.NumberColumn(format="₹%.2f"),
                "% From 52W High": st.column_config.NumberColumn(format="%.2f%%"),
                "RSI (14)": st.column_config.NumberColumn(format="%.2f"),
            }
        )

        col_dl, col_tv = st.columns([1, 1])
        with col_dl:
            st.download_button(
                label="📥 Download Results (CSV)",
                data=display_df.to_csv(index=False),
                file_name="nse_swing_plus_volume.csv",
                mime="text/csv",
                width="stretch",
            )
        with col_tv:
            tv_symbols = ",".join([f"NSE:{s}" for s in qualifying['symbol'].unique()])
            st.text_input("TradingView Watchlist String (Copy & Paste into TV):", value=tv_symbols)

        st.markdown("---")

        # Interactive Technical Stock Inspector Drilldown
        top_symbol = qualifying.iloc[0]["symbol"] if not qualifying.empty else None
        render_stock_inspector(indicators_df, default_symbol=top_symbol, key_prefix="swing_vol")

    except Exception as exc:
        logger.error("Swing + Volume page error: %s", exc, exc_info=True)
        st.error(f"Strategy error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
