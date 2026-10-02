"""
NSE Trading Terminal - Application Entrypoint
===========================================
Production-grade quantitative stock screening terminal for the National Stock Exchange of India.
Unified navigation directly to the Master Dashboard and Quantitative Strategies (no 'app' splash page).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure root directory is on PYTHONPATH for module imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("ui.app")

import streamlit as st
from ui.components.terminal_styles import apply_terminal_theme


def main() -> None:
    # Top-level application configuration
    st.set_page_config(
        page_title="NSE Trading Terminal | Quantitative Screener",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    apply_terminal_theme()

    curr_dir = Path(__file__).parent
    dashboard_page = st.Page(
        str(curr_dir / "Dashboard.py"),
        title="Master Dashboard",
        icon="📊",
        default=True,
    )
    swing_volume_page = st.Page(
        str(curr_dir / "pages" / "2_swing_volume.py"),
        title="Swing + Volume",
        icon="🌊",
    )
    swing_momentum_page = st.Page(
        str(curr_dir / "pages" / "3_swing_momentum.py"),
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
