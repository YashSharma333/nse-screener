import unittest
import pandas as pd
import numpy as np
from src.computation.indicators import TechnicalCalculator

class TestTechnicalCalculator(unittest.TestCase):
    def setUp(self):
        # Create a sample DataFrame
        dates = pd.date_range('2023-01-01', periods=300)
        np.random.seed(42)
        close_prices = np.random.uniform(100, 200, size=300)
        high_prices = close_prices + np.random.uniform(0, 10, size=300)
        
        self.df = pd.DataFrame({
            'date': dates,
            'close': close_prices,
            'high': high_prices
        })

    def test_calculate_sma(self):
        sma_50 = TechnicalCalculator.calculate_sma(self.df, window=50)
        self.assertEqual(len(sma_50), 300)
        self.assertTrue(pd.isna(sma_50.iloc[0]))
        self.assertFalse(pd.isna(sma_50.iloc[49]))
        
        # Test calculation manually for the 50th element (index 49)
        expected_sma = self.df['close'].iloc[0:50].mean()
        self.assertAlmostEqual(sma_50.iloc[49], expected_sma)

    def test_calculate_52_week_high(self):
        high_52w = TechnicalCalculator.calculate_52_week_high(self.df)
        self.assertEqual(len(high_52w), 300)
        self.assertFalse(pd.isna(high_52w.iloc[0]))
        
        # Manually check the max for the first 252 days
        expected_max = self.df['high'].iloc[0:252].max()
        self.assertAlmostEqual(high_52w.iloc[251], expected_max)

    def test_apply_all_indicators(self):
        df_result = TechnicalCalculator.apply_all_indicators(self.df.copy())
        self.assertIn('sma_50', df_result.columns)
        self.assertIn('sma_200', df_result.columns)
        self.assertIn('high_52w', df_result.columns)

if __name__ == '__main__':
    unittest.main()
