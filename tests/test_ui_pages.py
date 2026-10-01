from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestStreamlitPages(unittest.TestCase):
    def test_app_main_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "app.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"App page raised exception: {at.exception}")

    def test_dashboard_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "pages" / "1_dashboard.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Dashboard page raised exception: {at.exception}")

    def test_liquid_vol_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "pages" / "2_liquid_vol.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Liquid Vol page raised exception: {at.exception}")

    def test_liquid_momentum_page(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "ui" / "pages" / "3_liquid_momentum.py"), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Liquid Momentum page raised exception: {at.exception}")


if __name__ == "__main__":
    unittest.main()
