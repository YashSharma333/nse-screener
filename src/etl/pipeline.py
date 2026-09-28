import logging
from datetime import date
from typing import Optional, Tuple, List
import pandas as pd

logger = logging.getLogger(__name__)

class BhavcopyETL:
    """Full ingest-transform-load pipeline for NSE Bhavcopy data."""
    def __init__(self, initial_days_back: int = 1100) -> None:
        self.initial_days_back = initial_days_back
        # TODO: Initialize your downloader or DB connection here (e.g., SQLAlchemy engine)

    def resolve_date_range(self) -> Tuple[date, date, bool]:
        """Decide which date window to fetch by querying the database."""
        # TODO: Write a query using pandas read_sql to find the latest date in the db.
        # HINT: pd.read_sql("SELECT MAX(date) FROM daily_prices", con=your_db_engine)
        # return (start_date, end_date, is_update)
        pass

    def collect_new_rows(self, start: date, end: date, symbol_filter: Optional[set[str]] = None) -> pd.DataFrame:
        """Download Bhavcopy files for the date range and return a combined DataFrame."""
        # TODO: Loop over dates, download data, and concatenate them into a single pandas DataFrame.
        # HINT: Use pd.concat([df1, df2]) to combine data.
        pass

    def load_to_db(self, df: pd.DataFrame) -> Tuple[List[str], List[str]]:
        """Batch-insert the DataFrame into the database with upsert semantics."""
        # TODO: Use pandas to_sql to insert data into the database.
        # HINT: For upsert (avoiding duplicate primary keys), you might need a custom insertion method with to_sql(..., method=upsert_func)
        pass

    def run(self) -> None:
        """Execute the full ETL pipeline."""
        # TODO: Orchestrate the pipeline steps here.
        # 1. Resolve date range
        # 2. Collect new rows (as a DataFrame)
        # 3. Load DataFrame to DB
        pass
