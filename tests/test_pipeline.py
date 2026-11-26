import unittest

from src.db.models import DailyPrice


class TestModels(unittest.TestCase):
    def test_daily_price_model_index_name(self):
        # In task D4, index_name was dropped from daily_prices; series, market_cap, isin were added
        self.assertFalse(hasattr(DailyPrice, "index_name"))
        self.assertTrue(hasattr(DailyPrice, "series"))
        self.assertTrue(hasattr(DailyPrice, "market_cap"))
        self.assertTrue(hasattr(DailyPrice, "isin"))


if __name__ == "__main__":
    unittest.main()
