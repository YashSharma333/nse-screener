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
from ui.components.screener_view import render_screener_page, ribbon_html

setup_logging()


def ribbon(p: dict) -> str:
    """Condition chips built from the parameters in use, so sidebar edits show here."""
    low, high = p["ratio_180_252_min"], p["ratio_180_252_max"]
    return ribbon_html(
        [
            ("cyan", "🎯 Base: Meets Swing + Volume"),
            ("amber", f"🛡️ Anti-Climax: Return(60D) &le; {p['max_return_60d']:g}%"),
            ("purple", f"🚀 Multi-Quarter Ratio: &le; {p['ratio_252_180_max']:g}%"),
            ("green", f"📈 Base Velocity: [{low:g}%, {high:g}%]"),
            ("rose", f"🔥 Velocity: Return(120D) &ge; {p['min_return_120d']:g}%"),
        ]
    )


def main() -> None:
    render_screener_page(
        screener_key="swing_momentum",
        page_title="Swing With Momentum",
        subtitle="MOMENTUM ACCELERATION & TREND VELOCITY SCREENER",
        ribbon_builder=ribbon,
        key_prefix="swing_mom",
        csv_filename="nse_swing_with_momentum.csv",
    )


if __name__ == "__main__":
    main()
