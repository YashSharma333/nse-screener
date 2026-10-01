"""
Master Stock Screener Dashboard
===============================
Displays all tracked NSE equities with comprehensive quantitative indicators,
dynamic column customization, real-time filtering, and incremental ETL trigger.
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
logger = logging.getLogger("ui.dashboard")

import streamlit as st
import pandas as pd

from src.db.data_service import (
    load_raw_market_data,
    compute_market_indicators,
    get_latest_market_snapshot,
)
from src.etl.pipeline import BhavcopyETL
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header


def setup_page_config() -> None:
    st.set_page_config(
        page_title="Master Dashboard | ApexGrowth Terminal",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def initialize_session_state() -> None:
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
            st.session_state.market_data_cache = None
    except Exception as exc:
        logger.error("Incremental ETL trigger error: %s", exc, exc_info=True)
        st.session_state.etl_status = {"status": "error", "message": str(exc)}
    finally:
        st.session_state.is_updating_etl = False


def get_market_data():
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


def format_master_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw technical indicator columns to clean presentation names."""
    col_mapping = {
        'symbol': 'Symbol',
        'index_name': 'Index Category',
        'date': 'Date',
        'close': 'Close',
        'volume': 'Volume',
        'pct_change_daily': '% Change Daily',
        'pct_change_1m': '% Change 1M',
        'pct_change_3m': '% Change 3M',
        'pct_change_6m': '% Change 6M',
        'pct_change_12m': '% Change 12M',
        'sma_50': 'SMA 50',
        'sma_200': 'SMA 200',
        'ema_12': 'EMA 12',
        'ema_20': 'EMA 20',
        'ema_26': 'EMA 26',
        'rsi_14': 'RSI',
    }

    formatted = df.copy()
    existing_cols = [c for c in col_mapping.keys() if c in formatted.columns]
    formatted = formatted[existing_cols].rename(columns=col_mapping)
    return formatted


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()
        initialize_session_state()

        data = get_market_data()
        snapshot = data["snapshot"]
        is_live = data["is_live"]
        status_msg = data["status_msg"]

        render_terminal_header(
            title="NSE Master Market Screener",
            subtitle="CROSS-SECTIONAL TECHNICAL & QUANTITATIVE SNAPSHOT",
            status_text="LIVE MYSQL" if is_live else "DEMO MODE",
            is_live=is_live,
        )

        # Top Control Bar with Update Trigger
        col_ctrl1, col_ctrl2 = st.columns([3, 1])
        with col_ctrl1:
            st.caption(f"Status: {status_msg}")
        with col_ctrl2:
            if st.button(
                "🔄 Update Latest Market Data",
                disabled=st.session_state.is_updating_etl,
                use_container_width=True,
                help="Incrementally fetch and upsert today's Bhavcopy into MySQL",
            ):
                run_incremental_update()
                st.rerun()

        if st.session_state.etl_status:
            status = st.session_state.etl_status.get("status")
            msg = st.session_state.etl_status.get("message", "")
            if status == "success":
                st.success(f"✓ {msg}")
            elif status == "up_to_date":
                st.info(f"ℹ {msg}")
            else:
                st.error(f"✗ Update failed: {msg}")

        if snapshot.empty:
            st.info("No market data available to display. Please run the ETL pipeline.")
            return

        formatted_df = format_master_dataframe(snapshot)

        # Filters Row
        f1, f2, f3 = st.columns([1.5, 1.5, 3])
        with f1:
            indices = sorted(formatted_df['Index Category'].dropna().unique().tolist())
            selected_indices = st.multiselect("Index Filter", options=indices, default=indices)
        with f2:
            search_query = st.text_input("Search Symbol", placeholder="e.g. RELIANCE, TCS")
        with f3:
            all_display_cols = list(formatted_df.columns)
            primary_defaults = [
                'Symbol', 'Index Category', 'Date', 'Close', 'Volume',
                '% Change Daily', '% Change 1M', '% Change 3M', '% Change 6M', '% Change 12M',
                'SMA 50', 'SMA 200', 'EMA 12', 'EMA 20', 'EMA 26', 'RSI'
            ]
            valid_defaults = [c for c in primary_defaults if c in all_display_cols]
            selected_cols = st.multiselect(
                "Columns to Display",
                options=all_display_cols,
                default=valid_defaults,
            )

        # Apply Filters
        filtered_df = formatted_df.copy()
        if selected_indices:
            filtered_df = filtered_df[filtered_df['Index Category'].isin(selected_indices)]
        if search_query.strip():
            filtered_df = filtered_df[
                filtered_df['Symbol'].str.contains(search_query.strip().upper(), na=False)
            ]

        # Summary KPIs
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Displayed Equities", len(filtered_df))
        with kpi2:
            avg_daily_change = filtered_df['% Change Daily'].mean() if '% Change Daily' in filtered_df else 0.0
            st.metric("Mean Daily Change", f"{avg_daily_change:+.2f}%")
        with kpi3:
            advances = (filtered_df['% Change Daily'] > 0).sum() if '% Change Daily' in filtered_df else 0
            declines = (filtered_df['% Change Daily'] < 0).sum() if '% Change Daily' in filtered_df else 0
            st.metric("Advance / Decline", f"{advances} / {declines}")
        with kpi4:
            median_rsi = filtered_df['RSI'].median() if 'RSI' in filtered_df else 0.0
            st.metric("Median 14D RSI", f"{median_rsi:.1f}")

        st.markdown("---")

        # Master Data Table
        if not selected_cols:
            selected_cols = valid_defaults

        display_view = filtered_df[selected_cols].copy()

        # Formatting configurations
        column_config = {
            "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
            "Index Category": st.column_config.TextColumn("Index", width="medium"),
            "Date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "Close": st.column_config.NumberColumn("Close (₹)", format="₹%.2f"),
            "Volume": st.column_config.NumberColumn("Volume", format="%d"),
            "% Change Daily": st.column_config.NumberColumn("% Change Daily", format="%.2f%%"),
            "% Change 1M": st.column_config.NumberColumn("% Change 1M", format="%.2f%%"),
            "% Change 3M": st.column_config.NumberColumn("% Change 3M", format="%.2f%%"),
            "% Change 6M": st.column_config.NumberColumn("% Change 6M", format="%.2f%%"),
            "% Change 12M": st.column_config.NumberColumn("% Change 12M", format="%.2f%%"),
            "SMA 50": st.column_config.NumberColumn("SMA 50", format="₹%.2f"),
            "SMA 200": st.column_config.NumberColumn("SMA 200", format="₹%.2f"),
            "EMA 12": st.column_config.NumberColumn("EMA 12", format="₹%.2f"),
            "EMA 20": st.column_config.NumberColumn("EMA 20", format="₹%.2f"),
            "EMA 26": st.column_config.NumberColumn("EMA 26", format="₹%.2f"),
            "RSI": st.column_config.NumberColumn("RSI (14)", format="%.2f"),
        }

        st.dataframe(
            display_view,
            use_container_width=True,
            hide_index=True,
            column_config={k: v for k, v in column_config.items() if k in display_view.columns},
        )

        st.download_button(
            label="📥 Download Filtered Master Snapshot (CSV)",
            data=display_view.to_csv(index=False),
            file_name="nse_master_screener_snapshot.csv",
            mime="text/csv",
        )

    except Exception as exc:
        logger.error("Dashboard page crash: %s", exc, exc_info=True)
        st.error(f"Dashboard error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
