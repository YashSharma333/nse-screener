"""
Strategy 1: Liquid 1.5x Vol Screener
====================================
Screens for high-liquidity institutional accumulation within NIFTY 100,
NIFTY Midcap 150, and NIFTY Smallcap 250 indices.
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
logger = logging.getLogger("ui.liquid_vol")

import streamlit as st
import pandas as pd

from src.db.data_service import (
    load_raw_market_data,
    compute_market_indicators,
    get_latest_market_snapshot,
)
from src.screener import StockScreener
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header


def setup_page_config() -> None:
    st.set_page_config(
        page_title="Liquid 1.5x Vol Strategy | ApexGrowth",
        page_icon="🌊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def get_market_snapshot() -> tuple[pd.DataFrame, bool, str]:
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
    cache = st.session_state.market_data_cache
    return cache["snapshot"], cache["is_live"], cache["status_msg"]


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()

        snapshot, is_live, status_msg = get_market_snapshot()

        render_terminal_header(
            title="Strategy: Liquid 1.5x Vol",
            subtitle="INSTITUTIONAL VOLUME EXPANSION & TREND MOMENTUM FILTER",
            status_text="LIVE MYSQL" if is_live else "DEMO MODE",
            is_live=is_live,
        )

        # Strategy Specification Box
        st.markdown("""
        <div class="strategy-box">
            <div class="strategy-title">🎯 Quantitative Strategy Rationale</div>
            <div class="rule-item">1. <strong>Tracked Index Universe:</strong> Must belong to NIFTY 100, NIFTY Midcap 150, or NIFTY Smallcap 250 (eliminates penny/illiquid stocks).</div>
            <div class="rule-item">2. <strong>Moving Average Stack:</strong> EMA(20) > SMA(50) and SMA(50) > SMA(200) (strict multi-timeframe bullish trend).</div>
            <div class="rule-item">3. <strong>Price Dominance:</strong> Close > SMA(50) and Close > SMA(200).</div>
            <div class="rule-item">4. <strong>High Proximity:</strong> Close ≥ 0.85 × (1-day shifted 252-day High) (consolidating within 15% of annual peak).</div>
            <div class="rule-item">5. <strong>Liquidity Breakout:</strong> Volume > 1.5 × SMA(Volume, 20) and SMA(Volume, 20) > 250,000 shares.</div>
        </div>
        """, unsafe_allow_html=True)

        if snapshot.empty:
            st.info("Market data is empty. Run the ETL pipeline to ingest data.")
            return

        screener = StockScreener(snapshot)
        qualifying = screener.strategy_liquid_volume()

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
            st.info("No stocks currently satisfy all criteria for Strategy 1: Liquid 1.5x Vol.")
            return

        # Prepare Clean Display Table
        qualifying = qualifying.copy()
        qualifying['vol_multiple'] = qualifying['volume'] / qualifying['volume_sma_20']
        qualifying['pct_from_52w_high'] = ((qualifying['close'] - qualifying['high_252d_prev']) / qualifying['high_252d_prev']) * 100.0

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
            use_container_width=True,
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

        st.download_button(
            label="📥 Download Strategy 1 Results (CSV)",
            data=display_df.to_csv(index=False),
            file_name="nse_strategy1_liquid_vol.csv",
            mime="text/csv",
        )

    except Exception as exc:
        logger.error("Strategy 1 page error: %s", exc, exc_info=True)
        st.error(f"Strategy error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
