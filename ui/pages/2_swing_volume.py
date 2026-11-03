"""
Screener: Swing + Volume
=========================
Systematic screening for institutional volume expansion and multi-timeframe bullish trend alignment
across NIFTY 100, NIFTY Midcap 150, and NIFTY Smallcap 250 equities.
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
    <span class="filter-chip chip-cyan">🎯 Universe: Tracked Indices</span>
    <span class="filter-chip chip-green">📈 Trend: EMA(20) &gt; SMA(50) &gt; SMA(200)</span>
    <span class="filter-chip chip-cyan">💎 Price: Close &gt; SMA(50) &amp; SMA(200)</span>
    <span class="filter-chip chip-amber">⚡ Peak: Close &ge; 0.85 &times; 252D High</span>
    <span class="filter-chip chip-purple">🌊 Volume: Vol &gt; 1.5&times; 20D SMA</span>
    <span class="filter-chip chip-rose">💧 Liquidity: 20D SMA Vol &gt; 250K</span>
</div>
"""


def main() -> None:
    render_screener_page(
        screener_key="swing_volume",
        page_title="Swing + Volume",
        subtitle="INSTITUTIONAL VOLUME EXPANSION & MULTI-TIMEFRAME TREND BREAKOUT",
        ribbon_html=RIBBON_HTML,
        key_prefix="swing_vol",
        csv_filename="nse_swing_plus_volume.csv",
    )


if __name__ == "__main__":
    main()
