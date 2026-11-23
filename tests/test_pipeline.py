import unittest

from src.db.models import DailyPrice
from src.screeners.catalog import SwingMomentumScreener, SwingVolumeScreener


class TestModelsAndScreeners(unittest.TestCase):
    def test_daily_price_model_index_name(self):
        # In task D4, index_name was dropped from daily_prices; series, market_cap, isin were added
        self.assertFalse(hasattr(DailyPrice, "index_name"))
        self.assertTrue(hasattr(DailyPrice, "series"))
        self.assertTrue(hasattr(DailyPrice, "market_cap"))
        self.assertTrue(hasattr(DailyPrice, "isin"))

    def test_screener_empty_dataframe(self):
        import pandas as pd

        empty_df = pd.DataFrame()
        self.assertTrue(SwingVolumeScreener().run(empty_df).empty)
        self.assertTrue(SwingMomentumScreener().run(empty_df).empty)


if __name__ == "__main__":
    unittest.main()
