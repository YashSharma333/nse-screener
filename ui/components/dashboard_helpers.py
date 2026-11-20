"""Pure helpers for the Dashboard page (no Streamlit import, so they are easy to test).

Keeps the "what to show" logic (series filter, KPI numbers, column presets) apart from the
"how to draw it" code in ``ui/Dashboard.py``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Plain-English names for the NSE Bhavcopy series codes.
SERIES_LABELS: dict[str, str] = {
    "EQ": "EQ · Equity",
    "BE": "BE · Trade-to-trade",
    "BZ": "BZ · Trade-to-trade (surveillance)",
    "SM": "SM · SME",
    "ST": "ST · SME trade-to-trade",
}

# Raw snapshot column -> name shown in the table.
COLUMN_NAMES: dict[str, str] = {
    "symbol": "Symbol",
    "series": "Series",
    "close": "Close",
    "volume": "Volume",
    "pct_change_daily": "% Change Daily",
    "pct_change_1m": "% Change 1M",
    "pct_change_3m": "% Change 3M",
    "pct_change_6m": "% Change 6M",
    "pct_change_12m": "% Change 12M",
    "sma_50": "SMA 50",
    "sma_200": "SMA 200",
    "ema_12": "EMA 12",
    "ema_20": "EMA 20",
    "ema_26": "EMA 26",
    "rsi_14": "RSI",
    "pct_from_52w_high": "% From 52W High",
}

# Column presets so a beginner does not have to pick 15 columns one by one.
COLUMN_PRESETS: dict[str, list[str]] = {
    "Basic": ["Symbol", "Series", "Close", "% Change Daily", "Volume", "RSI"],
    "Trend": ["Symbol", "Series", "Close", "SMA 50", "SMA 200", "EMA 20", "% From 52W High"],
    "Momentum": [
        "Symbol",
        "Series",
        "Close",
        "% Change 1M",
        "% Change 3M",
        "% Change 6M",
        "% Change 12M",
        "RSI",
    ],
    "All": list(COLUMN_NAMES.values()),
}

NEAR_HIGH_PCT = 5.0  # "near the 52-week high" = within 5% of it
VOLUME_SURGE_X = 1.5  # same multiple the Swing + Volume screener uses


def rename_for_display(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Keep the display columns that exist in the snapshot and give them clean names."""
    present = [c for c in COLUMN_NAMES if c in snapshot.columns]
    return snapshot[present].rename(columns=COLUMN_NAMES)


def default_series(available: list[str]) -> list[str]:
    """Series pre-selected on first load: EQ when present, otherwise everything."""
    return ["EQ"] if "EQ" in available else list(available)


def filter_by_series(df: pd.DataFrame, series: list[str]) -> pd.DataFrame:
    """Keep only rows whose ``series`` is selected; a frame without a series column is kept."""
    if df.empty or "series" not in df.columns:
        return df
    return df[df["series"].isin(series)]


def filter_by_symbol(df: pd.DataFrame, text: str) -> pd.DataFrame:
    """Case-insensitive 'symbol contains text' search; empty text keeps everything."""
    text = text.strip()
    if not text or df.empty or "symbol" not in df.columns:
        return df
    return df[df["symbol"].str.contains(text, case=False, regex=False, na=False)]


def _share(mask: pd.Series, valid: pd.Series) -> float | None:
    """Percentage of valid rows where mask is true; None when nothing is valid."""
    count = int(valid.sum())
    return float(mask[valid].sum()) / count * 100.0 if count else None


def compute_dashboard_kpis(df: pd.DataFrame, total_rows: int) -> list[tuple[str, str, str]]:
    """Return ``(label, value, help)`` KPI tuples for the (already filtered) snapshot.

    ``total_rows`` is the size of the unfiltered snapshot, shown as "of N" in the help text.
    Missing data shows an em dash instead of a misleading 0.
    """
    dash = "—"

    def col(name: str) -> pd.Series:
        # A missing column becomes all-NaN with the same index, so comparisons still line up.
        return df[name] if name in df.columns else pd.Series(np.nan, index=df.index)

    daily = col("pct_change_daily")
    rsi = col("rsi_14")
    close, sma200 = col("close"), col("sma_200")
    from_high = col("pct_from_52w_high")
    vol_mult = col("vol_multiple")

    above200 = _share(close > sma200, close.notna() & sma200.notna()) if len(df) else None
    near_high = int((from_high >= -NEAR_HIGH_PCT).sum()) if from_high.notna().any() else None
    surge = int((vol_mult >= VOLUME_SURGE_X).sum()) if vol_mult.notna().any() else None

    return [
        ("Stocks shown", f"{len(df):,}", f"Out of {total_rows:,} stocks in the latest session."),
        ("Advancing", f"{int((daily > 0).sum()):,}", "Stocks that closed higher than yesterday."),
        ("Declining", f"{int((daily < 0).sum()):,}", "Stocks that closed lower than yesterday."),
        (
            "Mean change",
            f"{daily.mean():+.2f}%" if daily.notna().any() else dash,
            "Average % change today across the stocks shown.",
        ),
        (
            "Median RSI (14)",
            f"{rsi.median():.1f}" if rsi.notna().any() else dash,
            "Middle RSI value. Above 70 is overbought, below 30 oversold.",
        ),
        (
            "Above SMA 200",
            f"{above200:.0f}%" if above200 is not None else dash,
            "Share of stocks trading above their 200-day average (market breadth).",
        ),
        (
            "Near 52W high",
            f"{near_high:,}" if near_high is not None else dash,
            f"Stocks within {NEAR_HIGH_PCT:.0f}% of their 252-day high.",
        ),
        (
            f"Volume ≥ {VOLUME_SURGE_X}x",
            f"{surge:,}" if surge is not None else dash,
            f"Stocks trading at least {VOLUME_SURGE_X}x their 20-day average volume.",
        ),
    ]


def color_change(value: object) -> str:
    """CSS for a pandas Styler: green for gains, red for losses, nothing otherwise."""
    if isinstance(value, int | float) and pd.notna(value):
        if value > 0:
            return "color: #10b981"
        if value < 0:
            return "color: #f43f5e"
    return ""
