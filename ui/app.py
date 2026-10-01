"""
ApexGrowth Terminal - Application Entrypoint
===========================================
Production-grade quantitative stock screening terminal for the National Stock Exchange of India.
Provides unified, categorized navigation directly into the Master Dashboard and Quantitative Strategies.
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
from ui.components.terminal_styles import apply_terminal_theme, render_sidebar_controls


def main() -> None:
    st.set_page_config(
        page_title="ApexGrowth Terminal | NSE Quantitative Screener",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    apply_terminal_theme()
    render_sidebar_controls()

    # Define production pages with categorized navigation — eliminating the redundant "app" page
    pages_dir = Path(__file__).parent / "pages"
    dashboard_page = st.Page(
        str(pages_dir / "1_dashboard.py"),
        title="Master Dashboard",
        icon="📊",
        default=True,
    )
    swing_volume_page = st.Page(
        str(pages_dir / "2_swing_volume.py"),
        title="Swing + Volume",
        icon="🌊",
    )
    swing_momentum_page = st.Page(
        str(pages_dir / "3_swing_momentum.py"),
        title="Swing With Momentum",
        icon="🚀",
    )

    nav = st.navigation(
        {
            "Market Terminal": [dashboard_page],
            "Quantitative Strategies": [swing_volume_page, swing_momentum_page],
        }
    )
    nav.run()


if __name__ == "__main__":
    main()