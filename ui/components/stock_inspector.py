"""
Interactive Technical Stock Inspector & Candlestick Chart
=========================================================
Production-grade financial charting component for deep-dive technical analysis.
Provides candlestick price action, moving average overlays, volume expansion bars,
and RSI oscillator telemetry.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def render_stock_inspector(
    df_with_indicators: pd.DataFrame,
    default_symbol: str | None = None,
    key_prefix: str = "inspector",
) -> None:
    """Renders interactive candlestick, volume, and momentum charts for a chosen equity."""
    if df_with_indicators.empty or "symbol" not in df_with_indicators.columns:
        return

    all_symbols = sorted(df_with_indicators["symbol"].unique().tolist())
    if not all_symbols:
        return

    default_idx = 0
    if default_symbol and default_symbol in all_symbols:
        default_idx = all_symbols.index(default_symbol)

    st.markdown("### 🔍 Technical Stock Inspector")

    col_sel, col_lookback = st.columns([3, 1])
    with col_sel:
        selected_symbol = st.selectbox(
            "Select Ticker for Technical Deep Dive",
            options=all_symbols,
            index=default_idx,
            key=f"{key_prefix}_stock_select",
            help="Choose an equity to inspect candlestick action, moving average stacks, volume, and RSI.",
        )
    with col_lookback:
        lookback_days = st.selectbox(
            "Lookback Window",
            options=[60, 120, 180, 252, 500],
            index=3,
            format_func=lambda x: f"{x} Sessions",
            key=f"{key_prefix}_lookback",
        )

    # Filter data for chosen symbol
    stock_df = df_with_indicators[df_with_indicators["symbol"] == selected_symbol].copy()
    if stock_df.empty:
        st.warning(f"No historical records found for {selected_symbol}.")
        return

    stock_df = stock_df.sort_values(by="date")
    if len(stock_df) > lookback_days:
        stock_df = stock_df.iloc[-lookback_days:]

    latest = stock_df.iloc[-1]
    prev_close = stock_df.iloc[-2]["close"] if len(stock_df) > 1 else latest["close"]
    daily_change = ((latest["close"] - prev_close) / prev_close) * 100.0

    # Quick metric tiles
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric(
            label="Current Close",
            value=f"₹{latest['close']:,.2f}",
            delta=f"{daily_change:+.2f}%",
        )
    with m2:
        vol_multiple = (
            latest["volume"] / latest["volume_sma_20"]
            if ("volume_sma_20" in latest and latest["volume_sma_20"] > 0)
            else 1.0
        )
        st.metric(
            label="Volume Multiple",
            value=f"{vol_multiple:.2f}x",
            delta="Breakout" if vol_multiple >= 1.5 else "Normal",
            delta_color="normal" if vol_multiple >= 1.5 else "off",
        )
    with m3:
        rsi_val = latest.get("rsi_14", 50.0)
        rsi_status = "Overbought" if rsi_val >= 70 else ("Oversold" if rsi_val <= 30 else "Neutral")
        st.metric(label="RSI (14)", value=f"{rsi_val:.1f}", delta=rsi_status, delta_color="off")
    with m4:
        high_prev = latest.get("high_252d_prev", latest["close"])
        dist_52w = ((latest["close"] - high_prev) / high_prev * 100.0) if high_prev else 0.0
        st.metric(label="Distance from 52W High", value=f"{dist_52w:+.1f}%")
    with m5:
        idx_label = latest.get("index_name", "NSE Equity")
        st.metric(label="Index Classification", value=str(idx_label))

    # Construct Plotly Chart: Panel 1 = Candlestick + MAs, Panel 2 = Volume, Panel 3 = RSI
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.58, 0.22, 0.20],
        subplot_titles=(f"{selected_symbol} Price & MA Stack", "Volume & 20D SMA", "RSI (14)"),
    )

    # 1. Candlestick
    fig.add_trace(
        go.Candlestick(
            x=stock_df["date"],
            open=stock_df["open"],
            high=stock_df["high"],
            low=stock_df["low"],
            close=stock_df["close"],
            name="OHLC",
            increasing_line_color="#10b981",
            decreasing_line_color="#f43f5e",
        ),
        row=1,
        col=1,
    )

    # Overlays: EMA 20, SMA 50, SMA 200
    if "ema_20" in stock_df.columns:
        fig.add_trace(
            go.Scatter(
                x=stock_df["date"],
                y=stock_df["ema_20"],
                name="EMA 20",
                line=dict(color="#00f0ff", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "sma_50" in stock_df.columns:
        fig.add_trace(
            go.Scatter(
                x=stock_df["date"],
                y=stock_df["sma_50"],
                name="SMA 50",
                line=dict(color="#fbbf24", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "sma_200" in stock_df.columns:
        fig.add_trace(
            go.Scatter(
                x=stock_df["date"],
                y=stock_df["sma_200"],
                name="SMA 200",
                line=dict(color="#a855f7", width=1.5),
            ),
            row=1,
            col=1,
        )

    # 2. Volume Bars
    vol_colors = [
        "#10b981" if c >= o else "#f43f5e"
        for c, o in zip(stock_df["close"], stock_df["open"])
    ]
    fig.add_trace(
        go.Bar(
            x=stock_df["date"],
            y=stock_df["volume"],
            name="Volume",
            marker_color=vol_colors,
        ),
        row=2,
        col=1,
    )
    if "volume_sma_20" in stock_df.columns:
        fig.add_trace(
            go.Scatter(
                x=stock_df["date"],
                y=stock_df["volume_sma_20"],
                name="Vol SMA 20",
                line=dict(color="#f59e0b", width=1.5),
            ),
            row=2,
            col=1,
        )

    # 3. RSI
    if "rsi_14" in stock_df.columns:
        fig.add_trace(
            go.Scatter(
                x=stock_df["date"],
                y=stock_df["rsi_14"],
                name="RSI (14)",
                line=dict(color="#38bdf8", width=1.5),
            ),
            row=3,
            col=1,
        )
        # Reference thresholds at 70 and 30
        fig.add_hline(y=70, line_dash="dash", line_color="rgba(244, 63, 94, 0.6)", row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="rgba(16, 185, 129, 0.6)", row=3, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=620,
        margin=dict(l=30, r=30, t=40, b=20),
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10),
        ),
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0e1422",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#1e293b")
    fig.update_yaxes(showgrid=True, gridcolor="#1e293b")

    st.plotly_chart(fig, width="stretch")
