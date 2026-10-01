from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestStreamlitPages(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
