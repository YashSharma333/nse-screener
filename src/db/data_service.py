"""
Market Data Service Layer
=========================
Handles querying market data from MySQL with proactive error handling, caching,
technical indicator computations, and fallback demo dataset generation.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional, Tuple
import numpy as np
import pandas as pd
from sqlalchemy import text

from config.settings import setup_logging
from src.db.session import engine, check_connection
from src.computation.indicators import TechnicalCalculator

setup_logging()
logger = logging.getLogger(__name__)


def generate_demo_dataset(num_days: int = 300) -> pd.DataFrame:
    """Generate realistic synthetic multi-stock data for UI preview when DB is offline."""
    np.random.seed(42)
    end_dt = date.today()
    date_range = [end_dt - timedelta(days=i) for i in range(num_days * 2) if (end_dt - timedelta(days=i)).weekday() < 5]
    date_range = sorted(date_range[:num_days])

    stocks = [
        ("RELIANCE", "NIFTY 100", 2800.0, 1500000, 0.0004),
        ("TCS", "NIFTY 100", 3900.0, 800000, 0.0003),
        ("HDFCBANK", "NIFTY 100", 1600.0, 2000000, 0.0003),
        ("INFY", "NIFTY 100", 1750.0, 1200000, 0.0004),
        ("ICICIBANK", "NIFTY 100", 1200.0, 1800000, 0.0004),
        ("TATAMOTORS", "NIFTY 100", 950.0, 3500000, 0.0005),
        ("BHARTIARTL", "NIFTY 100", 1400.0, 900000, 0.0004),
        ("DIXON", "NIFTY Midcap 150", 11500.0, 450000, 0.0008),
        ("PERSISTENT", "NIFTY Midcap 150", 4800.0, 350000, 0.0005),
        ("COFORGE", "NIFTY Midcap 150", 6500.0, 320000, 0.0006),
        ("POLYCAB", "NIFTY Midcap 150", 6200.0, 400000, 0.0005),
        ("TRENT", "NIFTY Midcap 150", 7100.0, 600000, 0.0018),
        ("KAYNES", "NIFTY Smallcap 250", 4200.0, 310000, 0.0009),
        ("CAMS", "NIFTY Smallcap 250", 3800.0, 280000, 0.0004),
        ("CDSL", "NIFTY Smallcap 250", 1450.0, 850000, 0.0006),
        ("ANGELONE", "NIFTY Smallcap 250", 2700.0, 550000, 0.0007),
        ("DATAPATTNS", "NIFTY Smallcap 250", 2900.0, 320000, 0.0008),
    ]

    all_rows = []
    for symbol, index_name, base_price, base_vol, drift in stocks:
        cur_price = base_price * 0.75
        for idx, dt in enumerate(date_range):
            if symbol == "TRENT":
                # Staged institutional growth satisfying both Strategy 1 and Strategy 2
                if idx < 50:
                    d_drift = 0.0002
                elif idx < 120:
                    d_drift = 0.0020
                elif idx < 180:
                    d_drift = 0.0018
                elif idx < 240:
                    d_drift = 0.0022
                else:
                    d_drift = 0.0022
            elif symbol == "DIXON":
                d_drift = 0.0014
            else:
                d_drift = drift

            daily_shock = np.random.normal(d_drift, 0.010)
            cur_price *= (1.0 + daily_shock)
            high_price = cur_price * (1.0 + abs(np.random.normal(0.006, 0.003)))
            low_price = cur_price * (1.0 - abs(np.random.normal(0.006, 0.003)))
            open_price = (high_price + low_price) / 2.0

            # Last 2 days: institutional volume expansion (> 1.5x of 20d SMA) for specific breakouts
            if idx >= len(date_range) - 2 and symbol in ("TRENT", "DIXON"):
                vol_mult = 2.4
            else:
                vol_mult = np.random.uniform(0.85, 1.25)
            cur_vol = int(base_vol * vol_mult)

            all_rows.append({
                "symbol": symbol,
                "date": dt,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(cur_price, 2),
                "volume": cur_vol,
                "index_name": index_name,
            })

    return pd.DataFrame(all_rows)


def load_raw_market_data() -> Tuple[pd.DataFrame, bool, str]:
    """Fetch all daily prices from MySQL. Falls back to synthetic demo data if DB is unavailable."""
    if not check_connection():
        logger.warning("Database unavailable. Falling back to synthetic demonstration dataset.")
        df = generate_demo_dataset()
        return df, False, "Demo Mode (MySQL offline - using synthetic NIFTY dataset)"

    try:
        query = text("""
            SELECT symbol, date, open, high, low, close, volume, index_name
            FROM daily_prices
            ORDER BY symbol, date ASC
        """)
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)

        if df.empty:
            logger.warning("daily_prices table exists but is empty. Using demo dataset.")
            df = generate_demo_dataset()
            return df, False, "daily_prices is empty — run ETL pipeline or use demo mode"

        # Ensure correct data types
        df['date'] = pd.to_datetime(df['date']).dt.date
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        df['high'] = pd.to_numeric(df['high'], errors='coerce')
        df['low'] = pd.to_numeric(df['low'], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

        logger.info("Loaded %d daily price records across %d symbols from MySQL.", len(df), df['symbol'].nunique())
        return df, True, f"Live MySQL: {len(df):,} records ({df['symbol'].nunique()} symbols)"

    except Exception as exc:
        logger.error("Error reading from daily_prices: %s", exc, exc_info=True)
        df = generate_demo_dataset()
        return df, False, f"Database error ({exc}) — loaded demo dataset"


def compute_market_indicators(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Computes all technical and quantitative indicators grouped by stock symbol."""
    if raw_df.empty:
        return raw_df

    processed_list = []
    for _, group in raw_df.groupby('symbol', as_index=False):
        group_sorted = group.sort_values(by='date').copy()
        with_indicators = TechnicalCalculator.apply_all_indicators(group_sorted)
        processed_list.append(with_indicators)

    if not processed_list:
        return pd.DataFrame()

    return pd.concat(processed_list, ignore_index=True)


def get_latest_market_snapshot(with_indicators_df: pd.DataFrame) -> pd.DataFrame:
    """Extracts the most recent trading session row for each symbol."""
    if with_indicators_df.empty:
        return pd.DataFrame()

    latest = (
        with_indicators_df.sort_values(by=['symbol', 'date'])
        .groupby('symbol', as_index=False)
        .last()
    )
    return latest
