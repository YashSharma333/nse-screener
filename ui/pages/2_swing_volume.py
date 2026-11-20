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
from ui.components.screener_view import render_screener_page, ribbon_html

setup_logging()


def ribbon(p: dict) -> str:
    """Condition chips built from the parameters in use, so sidebar edits show here."""
    return ribbon_html(
        [
            ("cyan", "🎯 Universe: all stocks, grouped by series"),
            ("green", "📈 Trend: EMA(20) &gt; SMA(50) &gt; SMA(200)"),
            ("cyan", "💎 Price: Close &gt; SMA(50) &amp; SMA(200)"),
            (
                "amber",
                f"⚡ Peak: Close &ge; {1 - p['max_pct_from_high'] / 100:.2f} &times; 252D High",
            ),
            ("purple", f"🌊 Volume: Vol &gt; {p['vol_multiplier']:g}&times; 20D SMA"),
            ("rose", f"💧 Liquidity: 20D SMA Vol &gt; {p['min_volume']:,.0f}"),
        ]
    )


def main() -> None:
    render_screener_page(
        screener_key="swing_volume",
        page_title="Swing + Volume",
        subtitle="INSTITUTIONAL VOLUME EXPANSION & MULTI-TIMEFRAME TREND BREAKOUT",
        ribbon_builder=ribbon,
        key_prefix="swing_vol",
        csv_filename="nse_swing_plus_volume.csv",
    )


if __name__ == "__main__":
    main()
