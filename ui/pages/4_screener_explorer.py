"""
Screener Explorer & Diagnostic Terminal
========================================
Interactive explorer for all registered systematic screeners across categories
(Trend, Breakout, Pullback, Swing, Classic, Quantitative) featuring:
- Dynamic parameter adjustments
- Instant condition-level explainability
- Near-miss candidate detection (stocks failing by exactly 1 condition)
- Stock Inspector chart drilldowns
"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging

setup_logging()
logger = logging.getLogger("ui.screener_explorer")

import streamlit as st

from src.screeners.registry import get_screener, list_screeners
from ui.components.screener_sidebar import render_indicator_settings, render_market_cap_filter
from ui.components.screener_view import render_results
from ui.components.terminal_styles import apply_terminal_theme, render_terminal_header
from ui.data import get_snapshot, render_uninitialized_prompt


def setup_page_config() -> None:
    with contextlib.suppress(Exception):
        st.set_page_config(
            page_title="Screener Explorer | Trading Terminal",
            page_icon="🔬",
            layout="wide",
            initial_sidebar_state="expanded",
        )


def main() -> None:
    try:
        setup_page_config()
        apply_terminal_theme()

        data = get_snapshot()
        snapshot = data["snapshot"]

        render_terminal_header(
            title="Screener Explorer",
            subtitle="SYSTEMATIC SCREENING PLATFORM & NEAR-MISS DIAGNOSTICS",
            as_of=data["as_of"],
        )

        st.caption("ℹ️ *Prices are split/bonus adjusted.*")

        if snapshot.empty:
            render_uninitialized_prompt(data["message"])
            return

        # Sidebar Screener Selection
        st.sidebar.markdown("### 🔍 Select Screener")
        show_experimental = st.sidebar.checkbox(
            "Show experimental screeners",
            value=False,
            help="Include experimental screeners alongside production-ready core screeners.",
        )
        status_filter = None if show_experimental else "core"
        all_screeners = list_screeners(status=status_filter)
        if not all_screeners:
            st.warning("No screeners registered matching the selected status filter.")
            return

        categories = sorted({s.category.upper() for s in all_screeners})
        selected_cat = st.sidebar.selectbox("Category Filter", ["ALL", *categories])

        filtered_screeners = (
            all_screeners
            if selected_cat == "ALL"
            else [s for s in all_screeners if s.category.upper() == selected_cat]
        )

        screener_labels = {f"[{s.category.upper()}] {s.name}": s.key for s in filtered_screeners}
        selected_label = st.sidebar.selectbox("Screener", list(screener_labels.keys()))
        selected_key = screener_labels[selected_label]

        screener_cls = next(s for s in all_screeners if s.key == selected_key)

        # Screener Overview Card
        st.markdown(
            f"""
        <div style="background: rgba(30, 41, 59, 0.5); padding: 12px 18px; border-radius: 8px;
                    border-left: 4px solid #38bdf8; margin-bottom: 16px;">
            <div style="display: flex; gap: 10px; align-items: center;">
                <span style="background: #0284c7; color: #fff; font-size: 11px; padding: 2px 8px;
                             border-radius: 4px; font-weight: 600;">
                    {screener_cls.category.upper()}
                </span>
                <span style="color: #94a3b8; font-size: 12px;">
                    Version {screener_cls.version} &bull; Key: <code>{screener_cls.key}</code>
                </span>
            </div>
            <div style="color: #e2e8f0; font-size: 14px; margin-top: 6px;">
                {screener_cls.description}
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        # Sidebar settings for the chosen screener: market cap filter and indicator editor.
        # The key includes the screener, so each screener remembers its own values.
        prefix = f"explorer_{selected_key}"
        st.sidebar.markdown("### ⚙️ Screener settings")
        min_cr = render_market_cap_filter(prefix)
        overrides = render_indicator_settings(screener_cls, prefix)
        screener = get_screener(selected_key, **overrides)

        render_results(snapshot, screener, min_cr, f"{selected_key}_results", prefix)

    except Exception as exc:
        logger.error("Screener Explorer page error: %s", exc, exc_info=True)
        st.error(f"Explorer error: {exc}. Check logs/screener.log.")


if __name__ == "__main__":
    main()
