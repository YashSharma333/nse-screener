import unittest
from unittest.mock import patch, MagicMock
from datetime import date
import pandas as pd
from src.etl.pipeline import BhavcopyETL

class TestBhavcopyETL(unittest.TestCase):
    @patch('src.etl.pipeline.engine')
    @patch('src.etl.pipeline.NSEBhavcopyDownloader')
    def test_resolve_date_range_empty_db(self, MockDownloader, mock_engine):
        # Arrange
        etl = BhavcopyETL(initial_days_back=10)
        
        with patch('src.etl.pipeline.pd.read_sql') as mock_read_sql:
            mock_read_sql.return_value = pd.DataFrame({'last_date': [None]})
            
            # Act
            start, end, is_update = etl.resolve_date_range()
            
            # Assert
            self.assertFalse(is_update)
            self.assertEqual(start, end - pd.Timedelta(days=10))

    @patch('src.etl.pipeline.engine')
    @patch('src.etl.pipeline.NSEBhavcopyDownloader')
    def test_collect_new_rows(self, MockDownloader, mock_engine):
        mock_instance = MockDownloader.return_value
        mock_instance.download_bhavcopy.return_value = pd.DataFrame({
            'symbol': ['RELIANCE'], 'date': [date(2023, 1, 2)], 'close': [2500]
        })
        
        etl = BhavcopyETL()
        df = etl.collect_new_rows(date(2023, 1, 2), date(2023, 1, 2))
        
        self.assertFalse(df.empty)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]['symbol'], 'RELIANCE')

if __name__ == '__main__':
    unittest.main()
