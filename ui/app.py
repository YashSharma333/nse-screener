"""
ApexGrowth Terminal - Main Application Entrypoint
================================================
Production-grade quantitative stock screening terminal for the National Stock Exchange of India.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("ui.app")

import streamlit as st
import pandas as pd

from src.db.data_service import (
    load_raw_market_data,
    compute_market_indicators,
    get_latest_market_snapshot,
)
from src.screener import StockScreener
from src.etl.pipeline import BhavcopyETL
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header


def setup_page_config() -> None:
    st.set_page_config(
        page_title="ApexGrowth Terminal | NSE Quantitative Screener",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def initialize_session_state() -> None:
    """Safely initialize Streamlit session state keys."""
    if "etl_status" not in st.session_state:
        st.session_state.etl_status = None
    if "is_updating_etl" not in st.session_state:
        st.session_state.is_updating_etl = False
    if "market_data_cache" not in st.session_state:
        st.session_state.market_data_cache = None


def run_incremental_update() -> None:
    """Execute non-blocking incremental ETL update without freezing UI."""
    st.session_state.is_updating_etl = True
    try:
        with st.spinner("Fetching latest NSE Bhavcopy and updating MySQL..."):
            etl = BhavcopyETL()
            result = etl.run_incremental()
            st.session_state.etl_status = result
            # Invalidate cache so fresh data is loaded
            st.session_state.market_data_cache = None
    except Exception as exc:
        logger.error("Incremental ETL trigger error: %s", exc, exc_info=True)
        st.session_state.etl_status = {"status": "error", "message": str(exc)}
    finally:
        st.session_state.is_updating_etl = False


def render_sidebar() -> None:
    """Render global sidebar controls and terminal telemetry."""
    st.sidebar.markdown("### ⚡ Terminal Control")
    st.sidebar.caption("NSE Equity Quantitative Ingestion & Screening")

    st.sidebar.divider()

    st.sidebar.markdown("#### 🔄 Market Data Pipeline")
    if st.sidebar.button(
        "📥 Update Latest Market Data",
        disabled=st.session_state.is_updating_etl,
        width="stretch",
        help="Incrementally fetch only the most recent Bhavcopy data and upsert into MySQL",
    ):
        run_incremental_update()
        st.rerun()

    # Show notification if an update ran
    if st.session_state.etl_status:
        status = st.session_state.etl_status.get("status")
        msg = st.session_state.etl_status.get("message", "")
        if status == "success":
            st.sidebar.success(f"✓ {msg}")
        elif status == "up_to_date":
            st.sidebar.info(f"ℹ {msg}")
        else:
            st.sidebar.error(f"✗ Update failed: {msg}")

    st.sidebar.divider()
    st.sidebar.markdown("#### 📊 Tracked Index Universe")
    st.sidebar.markdown("""
    - **NIFTY 100**: Large Cap Core (90+ stocks)
    - **NIFTY Midcap 150**: Growth Midcaps (140+ stocks)
    - **NIFTY Smallcap 250**: High-Beta Smallcaps (230+ stocks)
    """)

    st.sidebar.divider()
    st.sidebar.caption("ApexGrowth Quantitative Terminal v2.0")


def load_cached_market_data():
    """Load or retrieve computed market indicators and snapshot."""
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
        initialize_session_state()
        render_sidebar()

        data = load_cached_market_data()
        snapshot = data["snapshot"]
        is_live = data["is_live"]
        status_msg = data["status_msg"]

        render_terminal_header(
            title="ApexGrowth Quantitative Terminal",
            subtitle="INSTITUTIONAL NSE EQUITY SCREENING & SYSTEMATIC ALPHA ENGINE",
            status_text="LIVE MYSQL" if is_live else "DEMO MODE",
            is_live=is_live,
        )

        if not is_live:
            st.warning(
                f"⚠️ **Notice:** {status_msg}. Running with synthetic NIFTY demonstration data. "
                "Ensure local MySQL is running with tables initialized (`python run_pipeline.py`)."
            )

        # Quantitative screener evaluation
        screener = StockScreener(snapshot)
        s1_results = screener.strategy_liquid_volume()
        s2_results = screener.strategy_liquid_momentum()

        latest_date = snapshot['date'].max() if 'date' in snapshot.columns and not snapshot.empty else "N/A"
        total_stocks = len(snapshot)

        # Overview Metrics Row
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(label="Tracked Equities", value=f"{total_stocks:,}")
        with col2:
            st.metric(label="Latest Session Date", value=str(latest_date))
        with col3:
            st.metric(label="Liquid 1.5x Vol Picks", value=f"{len(s1_results)}")
        with col4:
            st.metric(label="Liquid Momentum Picks", value=f"{len(s2_results)}")

        st.markdown("---")

        # Systematic Architecture & Portfolio Showcase Cards
        st.markdown("### 🎯 Quantitative Screening Architecture")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""
            <div class="strategy-box">
                <div class="strategy-title">Strategy 1: Liquid 1.5x Vol</div>
                <div class="rule-item">● <strong>Universe:</strong> NIFTY 100, Midcap 150, or Smallcap 250</div>
                <div class="rule-item">● <strong>MA Hierarchy:</strong> EMA(20) > SMA(50) > SMA(200)</div>
                <div class="rule-item">● <strong>Trend Alignment:</strong> Close > SMA(50) and Close > SMA(200)</div>
                <div class="rule-item">● <strong>52W High Proximity:</strong> Close ≥ 0.85 × Shifted 252d High</div>
                <div class="rule-item">● <strong>Liquidity Expansion:</strong> Volume > 1.5 × Volume SMA(20)</div>
                <div class="rule-item">● <strong>Institutional Size:</strong> Volume SMA(20) > 250,000 shares</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("Explore Strategy 1 Picks →", key="btn_nav_s1"):
                st.switch_page("pages/2_liquid_vol.py")

        with c2:
            st.markdown("""
            <div class="strategy-box">
                <div class="strategy-title">Strategy 2: Liquid 1.5x Vol Momentum</div>
                <div class="rule-item">● <strong>Base Filter:</strong> Satisfies all Strategy 1 criteria</div>
                <div class="rule-item">● <strong>Anti-Overheating:</strong> 60-day Return ≤ 40%</div>
                <div class="rule-item">● <strong>Long-term Ratio:</strong> ((Close - Close_252d) / Close_180d) × 100 ≤ 300%</div>
                <div class="rule-item">● <strong>Medium-term Ratio:</strong> ((Close - Close_180d) / Close_252d) × 100 ≤ 300%</div>
                <div class="rule-item">● <strong>Structural Base:</strong> ((Close - Close_180d) / Close_252d) × 100 ≥ 50%</div>
                <div class="rule-item">● <strong>Sustained Momentum:</strong> 120-day Return ≥ 30%</div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("Explore Strategy 2 Picks →", key="btn_nav_s2"):
                st.switch_page("pages/3_liquid_momentum.py")

        st.markdown("---")

        # Quick Preview Table
        st.markdown("### 📋 Market Snapshot Preview")
        preview_cols = [c for c in ['symbol', 'index_name', 'close', 'volume', 'pct_change_daily', 'rsi_14'] if c in snapshot.columns]
        display_preview = snapshot[preview_cols].copy()
        display_preview.columns = [c.replace('_', ' ').title() for c in display_preview.columns]
        st.dataframe(display_preview.head(10), width="stretch", hide_index=True)

        st.caption("Use the sidebar pages to navigate the Master Dashboard and filtered Quantitative Strategy views.")

    except Exception as exc:
        logger.error("Terminal crash in ui/app.py: %s", exc, exc_info=True)
        st.error(f"Application encountered an unexpected error: {exc}. Please review logs/screener.log.")


if __name__ == "__main__":
    main()