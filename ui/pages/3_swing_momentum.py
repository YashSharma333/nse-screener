"""
Screener: Swing With Momentum
==============================
Systematic screening combining Swing + Volume with medium-term momentum acceleration
and an anti-climax exhaustion guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging
from ui.components.screener_view import render_screener_page

setup_logging()

RIBBON_HTML = """
<div class="filter-ribbon">
    <span class="filter-chip chip-cyan">🎯 Base: Meets Swing + Volume</span>
    <span class="filter-chip chip-amber">🛡️ Anti-Climax: Return(60D) &le; 40%</span>
    <span class="filter-chip chip-purple">🚀 Multi-Quarter Ratio: &le; 300%</span>
    <span class="filter-chip chip-green">📈 Base Velocity: [50%, 300%]</span>
    <span class="filter-chip chip-rose">🔥 Velocity: Return(120D) &ge; 30%</span>
</div>
"""


def main() -> None:
    render_screener_page(
        screener_key="swing_momentum",
        page_title="Swing With Momentum",
        subtitle="MOMENTUM ACCELERATION & TREND VELOCITY SCREENER",
        ribbon_html=RIBBON_HTML,
        key_prefix="swing_mom",
        csv_filename="nse_swing_with_momentum.csv",
    )


if __name__ == "__main__":
    main()
