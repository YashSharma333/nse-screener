import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestStreamlitPages(unittest.TestCase):
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


class TestDashboardHasNoUpdateButton(unittest.TestCase):
    def test_no_button_can_load_or_rebuild_data(self):
        """The UI is read-only: no Sync or Update button exists on the dashboard (CL-06)."""
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "Dashboard.py"), default_timeout=30)
        at.run()
        labels = [b.label for b in at.button]
        self.assertFalse(any("Sync" in label or "Update" in label for label in labels), labels)


class TestDashboardFilters(unittest.TestCase):
    """Dashboard filters, run against a small fake snapshot so they work offline and in CI."""

    def _run_dashboard(self, snapshot):
        from unittest.mock import patch

        fake = {"snapshot": snapshot, "as_of": "2026-11-20", "message": ""}
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


class TestSwingVolumePageSidebar(unittest.TestCase):
    """Series tabs, EQ-only market cap filter and inspector list, on a crafted snapshot."""

    def _snapshot(self):
        import pandas as pd

        def row(symbol, series, mcap_cr, volume):
            return {
                "symbol": symbol,
                "series": series,
                "market_cap": mcap_cr * 1e7 if mcap_cr else float("nan"),
                "date": pd.Timestamp("2026-11-20").date(),
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "pct_change_daily": 1.0,
                "ema_20": 95.0,
                "sma_50": 90.0,
                "sma_200": 80.0,
                "high_252d_prev": 105.0,
                "pct_from_52w_high": (100.0 - 105.0) / 105.0 * 100.0,
                "volume": volume,
                "volume_sma_20": 400_000.0,
                "sma_volume_20_ago1": 400_000.0,  # yesterday's 20-day average (the surge rule)
                "rsi_14": 60.0,
            }

        rows = [
            row("AAA", "EQ", 1000, 900_000),  # passes
            row("BBB", "EQ", 100, 900_000),  # passes but below 500 cr -> hidden
            row("CCC", "SM", None, 900_000),  # passes; SME is never market cap filtered
            row("DDD", "EQ", 1000, 100_000),  # fails only volume_surge -> near-miss
        ]
        far = row("EEE", "EQ", 1000, 100_000)
        far.update(close=10.0, ema_20=1.0)  # fails several conditions
        return pd.DataFrame([*rows, far])

    def _run(self):
        from unittest.mock import patch

        fake = {"snapshot": self._snapshot(), "as_of": "2026-11-20", "message": ""}
        patcher = patch("ui.components.screener_view.get_snapshot", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        at = AppTest.from_file(
            str(PROJECT_ROOT / "ui" / "pages" / "2_swing_volume.py"), default_timeout=60
        )
        at.run()
        return at

    def test_tabs_per_series_and_market_cap_filter_is_eq_only(self):
        at = self._run()
        self.assertFalse(at.exception, f"Page raised: {at.exception}")
        self.assertEqual([t.label for t in at.tabs], ["EQ · 1", "SM · 1"])
        self.assertEqual(at.sidebar.number_input[0].value, 500.0)  # default

    def test_inspector_lists_only_the_results_on_the_page(self):
        """Regression: the ticker list must be passing + near-miss, not every stock."""
        at = self._run()
        select = next(s for s in at.selectbox if s.key == "swing_vol_stock_select")
        self.assertEqual(select.options, ["AAA", "CCC", "DDD"])

    def test_indicator_editor_changes_results_and_reset_restores(self):
        at = self._run()
        box = next(n for n in at.sidebar.number_input if n.label == "Vol multiplier")
        box.set_value(0.1).run()  # a tiny multiplier lets DDD pass as well
        select = next(s for s in at.selectbox if s.key == "swing_vol_stock_select")
        self.assertIn("DDD", select.options)
        self.assertEqual(next(t.label for t in at.tabs), "EQ · 2")
        next(b for b in at.sidebar.button if "Reset" in b.label).click().run()
        self.assertEqual(next(t.label for t in at.tabs), "EQ · 1")
