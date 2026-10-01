import unittest
import pandas as pd

from src.computation.screener import StockScreener

class TestStockScreener(unittest.TestCase):
    def setUp(self):
        # We'll create consecutive rows to allow shift() to work naturally.
        data = {
            'close': [100, 100, 100, 100, 100, 100, 100, 100, 91, 100],
            'sma_50': [85, 100, 110, 100, 100, 100, 100, 100, 100, 100],
            'sma_200': [90, 90, 100, 110, 100, 100, 100, 100, 100, 100],
            'high_52w': [150, 150, 150, 102, 150, 150, 150, 150, 150, 150],
            'low_52w': [50, 50, 50, 50, 98, 50, 50, 50, 50, 50],
            'rsi_14': [50, 50, 50, 50, 50, 25, 80, 50, 50, 50],
            'macd_line': [-2, 2, 0, 0, 0, 0, 0, 0, 0, 0],
            'macd_signal': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            'volatility_20': [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.04, 0.01, 0.01],
            'bb_upper': [110, 110, 110, 110, 110, 110, 110, 110, 110, 110],
            'bb_lower': [90, 90, 90, 90, 90, 90, 90, 90, 90, 90],
            'bb_middle': [100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        }
        self.df = pd.DataFrame(data)
        self.screener = StockScreener(self.df)

    def test_golden_cross(self):
        res = self.screener.golden_cross()
        self.assertIn(1, res.index)

    def test_death_cross(self):
        res = self.screener.death_cross()
        self.assertIn(3, res.index)

    def test_near_52_week_high(self):
        res = self.screener.near_52_week_high(threshold_pct=5.0)
        self.assertIn(3, res.index)

    def test_near_52_week_low(self):
        res = self.screener.near_52_week_low(threshold_pct=5.0)
        self.assertIn(4, res.index)

    def test_rsi_oversold(self):
        res = self.screener.rsi_oversold(threshold=30.0)
        self.assertIn(5, res.index)

    def test_rsi_overbought(self):
        res = self.screener.rsi_overbought(threshold=70.0)
        self.assertIn(6, res.index)

    def test_bullish_macd_crossover(self):
        res = self.screener.bullish_macd_crossover()
        self.assertIn(1, res.index)

    def test_high_volatility(self):
        res = self.screener.high_volatility(threshold=0.03)
        self.assertIn(7, res.index)

    def test_bollinger_squeeze(self):
        res = self.screener.bollinger_squeeze()
        self.assertIn(8, res.index)

if __name__ == '__main__':
    unittest.main()
