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

    def test_strategy_liquid_volume(self):
        # Create row that satisfies all conditions of Strategy 1
        s1_data = {
            'close': [100.0, 110.0],
            'ema_20': [90.0, 105.0],
            'sma_50': [80.0, 95.0],
            'sma_200': [70.0, 85.0],
            'high_252d_prev': [120.0, 115.0],  # 0.85 * 115 = 97.75 <= 110
            'volume': [100_000, 400_000],
            'volume_sma_20': [200_000, 260_000],  # 400_000 > 1.5 * 260_000 = 390_000, and 260k > 250k
            'index_name': ['NIFTY 100', 'NIFTY 100'],
        }
        df = pd.DataFrame(s1_data)
        screener = StockScreener(df)
        res = screener.strategy_liquid_volume()
        self.assertIn(1, res.index)
        self.assertNotIn(0, res.index)

    def test_strategy_liquid_momentum(self):
        # Row 1 satisfies Strategy 1 and all momentum conditions of Strategy 2:
        # 60d return <= 40% (here 20%)
        # 120d return >= 30% (here 35%)
        # Close = 200, Close_252d = 100, Close_180d = 120
        # ((Close - Close_252d) / Close_180d) * 100 = ((200 - 100) / 120) * 100 = 83.33% (<= 300)
        # ((Close - Close_180d) / Close_252d) * 100 = ((200 - 120) / 100) * 100 = 80.0% (between 50 and 300)
        s2_data = {
            'close': [200.0],
            'ema_20': [180.0],
            'sma_50': [160.0],
            'sma_200': [140.0],
            'high_252d_prev': [210.0],
            'volume': [500_000],
            'volume_sma_20': [300_000],
            'index_name': ['NIFTY Midcap 150'],
            'close_60d': [166.67],      # return ~20% <= 40%
            'close_120d': [148.15],     # return ~35% >= 30%
            'close_180d': [120.0],
            'close_252d': [100.0],
            'return_60d': [20.0],
            'return_120d': [35.0],
        }
        df = pd.DataFrame(s2_data)
        screener = StockScreener(df)
        res = screener.strategy_liquid_momentum()
        self.assertEqual(len(res), 1)
        self.assertIn(0, res.index)

    def test_screener_imports_from_both_locations(self):
        from src.screener import StockScreener as Screener1
        from src.computation.screener import StockScreener as Screener2
        self.assertIs(Screener1, Screener2)

if __name__ == '__main__':
    unittest.main()
