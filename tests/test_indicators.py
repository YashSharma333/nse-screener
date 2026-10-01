import unittest
import pandas as pd
import numpy as np

from src.computation.indicators import TechnicalCalculator

class TestTechnicalCalculator(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        dates = pd.date_range(end=pd.Timestamp.today(), periods=300)
        self.df = pd.DataFrame({
            'date': dates,
            'open': np.random.uniform(100, 200, 300),
            'high': np.random.uniform(150, 250, 300),
            'low': np.random.uniform(50, 150, 300),
            'close': np.random.uniform(100, 200, 300),
            'volume': np.random.randint(1000, 10000, 300)
        })
        self.calc = TechnicalCalculator()

    def test_calculate_sma(self):
        sma_20 = self.calc.calculate_sma(self.df, 20)
        self.assertEqual(len(sma_20), 300)
        self.assertTrue(pd.isna(sma_20.iloc[18]))
        self.assertFalse(pd.isna(sma_20.iloc[19]))

    def test_calculate_ema(self):
        ema_20 = self.calc.calculate_ema(self.df, 20)
        self.assertEqual(len(ema_20), 300)
        self.assertFalse(ema_20.isnull().all())

    def test_calculate_rsi(self):
        rsi = self.calc.calculate_rsi(self.df, 14)
        self.assertEqual(len(rsi), 300)
        # diff() produces NaN at index 0, rolling(14) needs 14 values,
        # so first valid RSI is at index 14.
        self.assertTrue(pd.isna(rsi.iloc[13]))
        self.assertFalse(pd.isna(rsi.iloc[14]))

    def test_calculate_52_week_high(self):
        high_52 = self.calc.calculate_52_week_high(self.df)
        self.assertEqual(len(high_52), 300)
        self.assertTrue(pd.isna(high_52.iloc[250])) 
        self.assertFalse(pd.isna(high_52.iloc[252]))

    def test_calculate_52_week_low(self):
        low_52 = self.calc.calculate_52_week_low(self.df)
        self.assertEqual(len(low_52), 300)
        self.assertTrue(pd.isna(low_52.iloc[250]))
        self.assertFalse(pd.isna(low_52.iloc[252]))

    def test_calculate_bollinger_bands(self):
        bb = self.calc.calculate_bollinger_bands(self.df, 20, 2)
        self.assertIn('upper', bb)
        self.assertIn('middle', bb)
        self.assertIn('lower', bb)
        self.assertEqual(len(bb['upper']), 300)
        
        # Test values where possible
        valid_idx = bb['middle'].dropna().index
        if len(valid_idx) > 0:
            idx = valid_idx[0]
            self.assertTrue(bb['upper'].loc[idx] >= bb['middle'].loc[idx])
            self.assertTrue(bb['middle'].loc[idx] >= bb['lower'].loc[idx])

    def test_calculate_macd(self):
        macd = self.calc.calculate_macd(self.df)
        self.assertIn('macd_line', macd)
        self.assertIn('signal_line', macd)
        self.assertIn('histogram', macd)
        self.assertEqual(len(macd['macd_line']), 300)

    def test_calculate_daily_returns(self):
        returns = self.calc.calculate_daily_returns(self.df)
        self.assertEqual(len(returns), 300)
        self.assertTrue(pd.isna(returns.iloc[0]))
        self.assertFalse(pd.isna(returns.iloc[1]))

    def test_calculate_volatility(self):
        vol = self.calc.calculate_volatility(self.df, 20)
        self.assertEqual(len(vol), 300)
        self.assertTrue(pd.isna(vol.iloc[19]))
        self.assertFalse(pd.isna(vol.iloc[20]))

    def test_apply_all_indicators(self):
        df_result = self.calc.apply_all_indicators(self.df.copy())
        expected_cols = [
            'sma_50', 'sma_200', 'ema_12', 'ema_26', 'rsi_14', 
            'high_52w', 'low_52w', 'bb_upper', 'bb_middle', 'bb_lower', 
            'macd_line', 'macd_signal', 'macd_histogram', 'daily_returns', 'volatility_20'
        ]
        for col in expected_cols:
            self.assertIn(col, df_result.columns)
            
    def test_edge_cases(self):
        empty_df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        single_df = self.df.iloc[[0]].copy()
        
        # Should handle empty gracefully
        res_empty = self.calc.apply_all_indicators(empty_df)
        self.assertEqual(len(res_empty), 0)
        
        # Should handle single row
        res_single = self.calc.apply_all_indicators(single_df)
        self.assertEqual(len(res_single), 1)

if __name__ == '__main__':
    unittest.main()
