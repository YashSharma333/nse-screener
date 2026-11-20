"""Pure helpers for the screener pages (no Streamlit import, so they are easy to test).

Covers: the EQ-only market cap filter, grouping results by NSE series, which screener
parameters the sidebar editor can change, and the tables shown on the result tabs.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

CRORE = 1e7  # 1 crore rupees; market cap is stored in rupees
DEFAULT_MIN_MARKET_CAP_CR = 500.0
MARKET_CAP_SERIES = "EQ"  # the market cap rule only applies to this series


def apply_market_cap_filter(df: pd.DataFrame, min_cr: float) -> pd.DataFrame:
    """Drop EQ stocks below ``min_cr`` crore; other series (SM, BE, ...) are never filtered.

    Why EQ only: the owner's 500 cr rule is a quality bar for normal equities, while SME and
    restricted series are screened on their own. An EQ stock with no market cap cannot prove
    it passes, so it is dropped when the filter is on. ``min_cr <= 0`` switches it off.
    """
    if df.empty or min_cr <= 0 or "market_cap" not in df.columns:
        return df
    big_enough = df["market_cap"] >= min_cr * CRORE  # NaN compares False -> dropped
    if "series" not in df.columns:
        return df[big_enough]
    return df[(df["series"] != MARKET_CAP_SERIES) | big_enough]


def group_by_series(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Split rows by series, EQ first then alphabetical. Without a series column: one group."""
    if df.empty:
        return []
    if "series" not in df.columns:
        return [("All", df)]
    names = sorted(df["series"].dropna().unique().tolist(), key=lambda s: (s != "EQ", s))
    return [(name, df[df["series"] == name]) for name in names]


def editable_params(default_params: dict[str, Any]) -> dict[str, int | float]:
    """Numeric screener parameters the sidebar editor can change (bools are skipped)."""
    return {
        name: value
        for name, value in default_params.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }


def param_step(default: float) -> float:
    """A sensible +/- step for a number box: one tenth of the default's order of magnitude."""
    if default == 0:
        return 0.1
    return float(10 ** (math.floor(math.log10(abs(default))) - 1))


def changed_params(
    values: dict[str, int | float], defaults: dict[str, int | float]
) -> dict[str, int | float]:
    """Only the values that differ from the defaults (these become screener overrides)."""
    return {name: value for name, value in values.items() if value != defaults.get(name)}


def _with_market_cap_cr(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``market_cap_cr`` (crores, NaN when unknown) so the table can sort it as a number."""
    out = df.copy()
    if "market_cap" in out.columns:
        out["market_cap_cr"] = (out["market_cap"] / CRORE).where(out["market_cap"] > 0)
    return out


def build_results_table(passing: pd.DataFrame) -> pd.DataFrame:
    """Columns shown for passing stocks (adds volume multiple and market cap in crores)."""
    res = _with_market_cap_cr(passing)
    if "vol_multiple" not in res.columns and {"volume", "volume_sma_20"} <= set(res.columns):
        res["vol_multiple"] = res["volume"] / res["volume_sma_20"].replace(0, np.nan)
    order = [
        "symbol",
        "series",
        "market_cap_cr",
        "close",
        "pct_change_daily",
        "volume",
        "vol_multiple",
        "ema_20",
        "sma_50",
        "sma_200",
        "rsi_14",
    ]
    return res[[c for c in order if c in res.columns]]


def build_near_miss_table(near: pd.DataFrame) -> pd.DataFrame:
    """Columns shown for near-miss stocks (the one condition they failed is the bottleneck)."""
    res = _with_market_cap_cr(near)
    order = [
        "symbol",
        "series",
        "market_cap_cr",
        "close",
        "near_miss_on",
        "conditions_passed",
        "conditions_total",
    ]
    return res[[c for c in order if c in res.columns]].rename(
        columns={"near_miss_on": "failed_condition"}
    )


def inspector_symbols(explained: pd.DataFrame) -> list[str]:
    """Symbols for the stock inspector: passing stocks first, then near-misses, no others."""
    if explained.empty or "symbol" not in explained.columns:
        return []
    passing = explained.loc[explained["passed"], "symbol"].sort_values().tolist()
    near = explained.loc[explained["near_miss"] & ~explained["passed"], "symbol"]
    return passing + near.sort_values().tolist()
