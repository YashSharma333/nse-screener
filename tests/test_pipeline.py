import unittest
from unittest.mock import patch, MagicMock
from src.ingestion.index_constituents import ConstituentResult, NSEIndexConstituents
from src.db.models import DailyPrice
from src.screener import StockScreener


class TestIngestionAndModels(unittest.TestCase):
    def test_constituent_result_dict_interface(self):
        mapping = {
            "RELIANCE": "NIFTY 100",
            "DIXON": "NIFTY Midcap 150",
            "KAYNES": "NIFTY Smallcap 250",
        }
        res = ConstituentResult(
            symbol_to_index=mapping,
            symbols=set(mapping.keys()),
            per_index={"NIFTY 100": {"RELIANCE"}},
        )
        # Verify dict behaviors
        self.assertEqual(res["RELIANCE"], "NIFTY 100")
        self.assertEqual(res.get("DIXON"), "NIFTY Midcap 150")
        self.assertEqual(len(res), 3)
        self.assertIn("KAYNES", res)
        self.assertEqual(res.symbols, {"RELIANCE", "DIXON", "KAYNES"})

    def test_daily_price_model_index_name(self):
        self.assertTrue(hasattr(DailyPrice, "index_name"))
        col = getattr(DailyPrice, "index_name")
        self.assertEqual(col.name, "index_name")

    def test_screener_empty_dataframe(self):
        import pandas as pd
        empty_df = pd.DataFrame()
        screener = StockScreener(empty_df)
        self.assertTrue(screener.strategy_liquid_volume().empty)
        self.assertTrue(screener.strategy_liquid_momentum().empty)


if __name__ == "__main__":
    unittest.main()
