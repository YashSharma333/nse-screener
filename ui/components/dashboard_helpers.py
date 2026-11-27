"""Pure helpers for the Dashboard page (no Streamlit import, so they are easy to test).

Keeps the "what to show" logic (series filter, KPI numbers, column presets) apart from the
"how to draw it" code in ``ui/Dashboard.py``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.computation.indicators.price_helpers import pct_diff, ratio
from src.computation.indicators.resolver import ind

# Plain-English names for the NSE Bhavcopy series codes.
SERIES_LABELS: dict[str, str] = {
    "EQ": "EQ · Equity",
    "BE": "BE · Trade-to-trade",
    "BZ": "BZ · Trade-to-trade (surveillance)",
    "SM": "SM · SME",
    "ST": "ST · SME trade-to-trade",
}

# The indicator columns the Dashboard shows. Most are saved in the cache; the rest (percent
# changes, EMA 12 and 26, yesterday's volume average) are computed when the page loads.
DASHBOARD_INDICATORS = [
    *[ind("percent_change", length=days) for days in (1, 21, 63, 126, 252)],
    ind("sma", length=50),
    ind("sma", length=200),
    ind("ema", length=12),
    ind("ema", length=20),
    ind("ema", length=26),
    ind("rsi", length=14),
    ind("window", source="high", length=252, stat="max", days_ago=1),
    ind("sma", source="volume", length=20, days_ago=1),
]

# Column names (the resolver's names) used below, written once.
CHANGE_DAILY = "percent_change_close_1"
SMA_200 = "sma_close_200"
RSI_14 = "rsi_close_14"
PRIOR_HIGH_252 = "window_high_252_max_ago1"
PRIOR_VOLUME_AVERAGE = "sma_volume_20_ago1"

# Raw snapshot column -> (name shown in the table, number format or None for text columns).
# The format is a Streamlit/printf string: "₹%.2f" is rupees, "%.2f%%" is a percentage.
COLUMN_DISPLAY: dict[str, tuple[str, str | None]] = {
    "symbol": ("Symbol", None),
    "series": ("Series", None),
    "close": ("Close", "₹%.2f"),
    "volume": ("Volume", "%d"),
    CHANGE_DAILY: ("% Change Daily", "%.2f%%"),
    "percent_change_close_21": ("% Change 1M", "%.2f%%"),
    "percent_change_close_63": ("% Change 3M", "%.2f%%"),
    "percent_change_close_126": ("% Change 6M", "%.2f%%"),
    "percent_change_close_252": ("% Change 12M", "%.2f%%"),
    "sma_close_50": ("SMA 50", "₹%.2f"),
    SMA_200: ("SMA 200", "₹%.2f"),
    "ema_close_12": ("EMA 12", "₹%.2f"),
    "ema_close_20": ("EMA 20", "₹%.2f"),
    "ema_close_26": ("EMA 26", "₹%.2f"),
    RSI_14: ("RSI", "%.1f"),
    "pct_from_52w_high": ("% From 52W High", "%.1f%%"),
}

# Names of the % change columns: the table colours these green and red.
PERCENT_CHANGE_LABELS = [
    label for label, _ in COLUMN_DISPLAY.values() if label.startswith("% Change")
]

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
    "All": [label for label, _ in COLUMN_DISPLAY.values()],
}

NEAR_HIGH_PCT = 5.0  # "near the 52-week high" = within 5% of it
VOLUME_SURGE_X = 1.5  # same multiple the shipped strategies use


def add_derived_columns(table: pd.DataFrame) -> pd.DataFrame:
    """Add ``pct_from_52w_high`` and ``vol_multiple`` from columns the table already has.

    Both compare today with YESTERDAY's value (the same rule the strategies use): the close
    against the prior-day 252-day high, and the volume against the prior-day 20-day average.
    A table without the needed columns is returned unchanged.
    """
    table = table.copy()
    if {"close", PRIOR_HIGH_252}.issubset(table.columns):
        table["pct_from_52w_high"] = pct_diff(table["close"], table[PRIOR_HIGH_252])
    if {"volume", PRIOR_VOLUME_AVERAGE}.issubset(table.columns):
        table["vol_multiple"] = ratio(table["volume"], table[PRIOR_VOLUME_AVERAGE])
    return table


def rename_for_display(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Keep the display columns that exist in the snapshot and give them clean names."""
    present = [c for c in COLUMN_DISPLAY if c in snapshot.columns]
    return snapshot[present].rename(columns={c: COLUMN_DISPLAY[c][0] for c in present})


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

    daily = col(CHANGE_DAILY)
    rsi = col(RSI_14)
    close, sma200 = col("close"), col(SMA_200)
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
