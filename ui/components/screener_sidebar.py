"""Sidebar widgets shared by every screener page: market cap filter and indicator settings."""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.screeners.base import Screener
from ui.components.screener_helpers import (
    DEFAULT_MIN_MARKET_CAP_CR,
    changed_params,
    editable_params,
    param_step,
)


def render_market_cap_filter(key_prefix: str) -> float:
    """Number box for the minimum market cap in crore (EQ stocks only). Returns the value."""
    return float(
        st.sidebar.number_input(
            "Min market cap (₹ Cr)",
            min_value=0.0,
            value=DEFAULT_MIN_MARKET_CAP_CR,
            step=100.0,
            key=f"{key_prefix}_min_mcap",
            help="Applies to the EQ series only. SME and other series are not filtered. "
            "Set 0 to switch it off.",
        )
    )


def render_indicator_settings(screener_cls: type[Screener], key_prefix: str) -> dict[str, Any]:
    """Sidebar editor for the screener's numeric parameters.

    Returns only the values the user changed, to be passed as ``get_screener(key, **overrides)``.
    Changes live in the session only; the locked defaults in the screener code never change.
    """
    defaults = editable_params(screener_cls.default_params)
    if not defaults:
        return {}

    keys = {name: f"{key_prefix}_param_{name}" for name in defaults}

    def reset_to_defaults() -> None:
        for name, key in keys.items():
            st.session_state[key] = defaults[name]

    values: dict[str, int | float] = {}
    with st.sidebar.expander("🎛️ Indicator settings", expanded=False):
        for name, default in defaults.items():
            label = name.replace("_", " ").capitalize()
            if isinstance(default, int):
                values[name] = st.number_input(
                    label, value=default, step=1, key=keys[name], help=f"Default: {default}"
                )
            else:
                values[name] = st.number_input(
                    label,
                    value=float(default),
                    step=param_step(default),
                    key=keys[name],
                    help=f"Default: {default}",
                )
        st.button("↩️ Reset to defaults", key=f"{key_prefix}_reset", on_click=reset_to_defaults)

    overrides = changed_params(values, defaults)
    if overrides:
        st.sidebar.caption(f"✏️ {len(overrides)} setting(s) changed from the defaults.")
    return overrides
