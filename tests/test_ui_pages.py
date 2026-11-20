import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestStreamlitPages(unittest.TestCase):
    def test_app_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "app.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"App router raised exception: {at.exception}")

    def test_dashboard_main_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Dashboard page raised exception: {at.exception}")

    def test_swing_volume_page(self):
        at = AppTest.from_file(
            str(PROJECT_ROOT / "ui" / "pages" / "2_swing_volume.py"), default_timeout=30
        )
        at.run()
        self.assertFalse(at.exception, f"Swing + Volume page raised exception: {at.exception}")

    def test_swing_momentum_page(self):
        at = AppTest.from_file(
            str(PROJECT_ROOT / "ui" / "pages" / "3_swing_momentum.py"), default_timeout=30
        )
        at.run()
        self.assertFalse(at.exception, f"Swing With Momentum page raised exception: {at.exception}")

    def test_screener_explorer_page(self):
        at = AppTest.from_file(
            str(PROJECT_ROOT / "ui" / "pages" / "4_screener_explorer.py"), default_timeout=30
        )
        at.run()
        self.assertFalse(at.exception, f"Screener Explorer page raised exception: {at.exception}")

    def test_stock_inspector_renders_candlestick(self):
        import pandas as pd

        from ui.components import stock_inspector

        df = pd.DataFrame(
            [
                {
                    "symbol": "TEST",
                    "date": "2024-01-01",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "volume": 10000,
                    "ema_20": 100.0,
                    "sma_50": 98.0,
                    "sma_200": 90.0,
                    "volume_sma_20": 8000,
                    "rsi_14": 55.0,
                },
                {
                    "symbol": "TEST",
                    "date": "2024-01-02",
                    "open": 102.0,
                    "high": 108.0,
                    "low": 101.0,
                    "close": 107.0,
                    "volume": 15000,
                    "ema_20": 101.0,
                    "sma_50": 98.5,
                    "sma_200": 90.5,
                    "volume_sma_20": 8500,
                    "rsi_14": 58.0,
                },
            ]
        )
        try:
            stock_inspector.render_stock_inspector(df, default_symbol="TEST")
        except Exception as e:
            self.fail(f"render_stock_inspector raised an exception: {e}")


if __name__ == "__main__":
    unittest.main()


class TestDashboardSingleUpdateButton(unittest.TestCase):
    def test_update_button_only_in_sidebar(self):
        """The data sync button must exist once (sidebar), not twice (regression)."""
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=30)
        at.run()
        labels = [b.label for b in at.button]
        self.assertEqual(sum("Sync Market Data" in label for label in labels), 1)
        self.assertFalse(any("Update Latest Market Data" in label for label in labels))


class TestDashboardFilters(unittest.TestCase):
    """Dashboard filters, run against a small fake snapshot so they work offline and in CI."""

    def _run_dashboard(self, snapshot):
        from unittest.mock import patch

        fake = {"snapshot": snapshot, "is_live": True, "status_msg": "", "message": ""}
        # Keep the patch alive until the test ends: widget changes re-run the script.
        patcher = patch("ui.data.get_snapshot", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=60)
        at.run()
        return at

    def _snapshot(self):
        import pandas as pd

        return pd.DataFrame(
            {
                "symbol": ["AAA", "BBB", "CCC"],
                "series": ["EQ", "EQ", "SM"],
                "date": [pd.Timestamp("2026-11-20").date()] * 3,
                "close": [100.0, 50.0, 20.0],
                "volume": [1000, 2000, 3000],
                "pct_change_daily": [1.0, -2.0, 0.5],
                "rsi_14": [55.0, 45.0, 50.0],
            }
        )

    def test_series_selector_defaults_to_eq_and_has_no_market_cap_filter(self):
        at = self._run_dashboard(self._snapshot())
        self.assertFalse(at.exception, f"Dashboard raised: {at.exception}")
        series = next(m for m in at.multiselect if m.label == "Series")
        self.assertEqual(series.value, ["EQ"])
        self.assertEqual(at.metric[0].value, "2")  # Stocks shown: the 2 EQ rows
        self.assertFalse(any("Market Cap" in n.label for n in at.number_input))

        series.set_value(["EQ", "SM"]).run()
        self.assertEqual(at.metric[0].value, "3")

    def test_dashboard_survives_cache_without_series_column(self):
        """Regression: an older cache has no series column; the page must not crash."""
        at = self._run_dashboard(self._snapshot().drop(columns=["series"]))
        self.assertFalse(at.exception)
        self.assertEqual(len(at.error), 0, [e.value for e in at.error])
        self.assertEqual(at.metric[0].value, "3")
