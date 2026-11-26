"""
Tests for strategy entry signals on a price history, and the sparse projection (T13, CL-24C).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.signals import evaluate_entry, to_sparse
from src.strategies.spec import EntrySpec

# Close above its 50-day average AND volume above 1.2 times its 20-day average as of yesterday
ENTRY = EntrySpec.model_validate(
    {
        "condition": {
            "type": "group",
            "mode": "all",
            "children": [
                {
                    "type": "compare",
                    "op": ">",
                    "left": {"type": "field", "name": "close"},
                    "right": {"type": "indicator", "name": "sma", "params": {"length": 50}},
                },
                {
                    "type": "compare",
                    "op": ">",
                    "left": {"type": "field", "name": "volume"},
                    "right": {
                        "type": "math",
                        "op": "*",
                        "left": {"type": "number", "value": 1.2},
                        "right": {
                            "type": "indicator",
                            "name": "sma",
                            "params": {"length": 20},
                            "source": {"type": "field", "name": "volume"},
                            "days_ago": 1,
                        },
                    },
                },
            ],
        }
    }
)


def _panel(symbols: list[str], days: int, seed: int) -> pd.DataFrame:
    """A seeded random-walk price history with volume, many days per stock."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=days).strftime("%Y-%m-%d").tolist()
    frames = []
    for symbol in symbols:
        close = 1000.0 * np.cumprod(1.0 + rng.normal(0.001, 0.01, days))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": dates,
                    "open": close * 0.99,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": rng.integers(100_000, 400_000, days).astype(float),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_evaluate_entry_on_a_panel_and_to_sparse():
    """The entry gives one true/false per stock-day; to_sparse keeps only the true ones."""
    panel = _panel(["INFY", "TCS"], 100, seed=42)
    signals_df = panel[["symbol", "date", "close"]].copy()
    signals_df["signal"] = evaluate_entry(ENTRY, panel)

    assert len(signals_df) == len(panel)
    assert signals_df["signal"].dtype == bool
    assert not signals_df["signal"].iloc[:49].any()  # no 50-day average yet: never a signal
    assert signals_df["signal"].any()  # the seeded data does produce signals

    sparse_df = to_sparse(signals_df)
    assert (sparse_df["signal"] == True).all()  # noqa: E712
    assert len(sparse_df) == int(signals_df["signal"].sum())


def test_to_sparse_of_an_empty_or_signal_less_frame_is_empty():
    assert to_sparse(pd.DataFrame()).empty
    assert to_sparse(pd.DataFrame({"symbol": ["A"]})).empty


def test_signals_point_in_time_truncation():
    """Signals generated at date t must remain invariant when future data is truncated."""
    panel = _panel(["RELIANCE", "HDFCBANK"], 150, seed=123)
    t_eval = sorted(panel["date"].unique())[100]
    truncated = panel[panel["date"] <= t_eval].reset_index(drop=True)

    full_signals = panel[["symbol", "date"]].assign(signal=evaluate_entry(ENTRY, panel))
    trunc_signals = truncated[["symbol", "date"]].assign(signal=evaluate_entry(ENTRY, truncated))

    full_at_t = full_signals[full_signals["date"] == t_eval].sort_values("symbol")
    trunc_at_t = trunc_signals[trunc_signals["date"] == t_eval].sort_values("symbol")
    assert full_at_t["signal"].tolist() == trunc_at_t["signal"].tolist()
    # and on every earlier day too, not only the last one
    earlier = full_signals[full_signals["date"] <= t_eval].reset_index(drop=True)
    assert earlier["signal"].tolist() == trunc_signals["signal"].tolist()
