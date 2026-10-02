from pathlib import Path
import unittest
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
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "pages" / "2_swing_volume.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Swing + Volume page raised exception: {at.exception}")

    def test_swing_momentum_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "pages" / "3_swing_momentum.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Swing With Momentum page raised exception: {at.exception}")

    def test_stock_inspector_fallback_when_plotly_missing(self):
        from unittest.mock import patch
        import pandas as pd
        from ui.components import stock_inspector

        df = pd.DataFrame([
            {"symbol": "TEST", "date": "2024-01-01", "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 10000},
            {"symbol": "TEST", "date": "2024-01-02", "open": 102.0, "high": 108.0, "low": 101.0, "close": 107.0, "volume": 15000},
        ])
        with patch.object(stock_inspector, "HAS_PLOTLY", False):
            try:
                stock_inspector.render_stock_inspector(df, default_symbol="TEST")
            except Exception as e:
                self.fail(f"render_stock_inspector raised an exception when HAS_PLOTLY=False: {e}")


if __name__ == "__main__":
    unittest.main()
