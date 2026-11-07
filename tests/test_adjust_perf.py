"""Benchmark performance tests for corporate actions adjustment engine (G7)."""

from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, DailyPrice
from src.etl.adjust import adjust_new_rows, rebuild_adjusted


def test_adjust_benchmark_sqlite_timing():
    """Benchmark full rebuild and daily incremental time on SQLite.

    Creates a synthetic dataset of 500 symbols x 2500 days (1,250,000 rows).
    Measures:
    1. Full rebuild execution time
    2. Incremental adjust_new_rows execution time for a new day
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    n_symbols = 500
    n_days = 2500
    start_date = date(2014, 1, 1)
    symbols = [f"SYM_{i:04d}" for i in range(n_symbols)]

    # Generate dates
    dates = [start_date + timedelta(days=i) for i in range(n_days)]

    # Fast bulk insertion using raw SQLite connection
    conn = engine.raw_connection()
    cursor = conn.cursor()

    # Pre-generate rows
    np.random.default_rng(42)
    # 5 symbols have a 1:2 split at day 1000
    split_symbols = set(symbols[:5])

    records = []
    for s_idx, sym in enumerate(symbols):
        base_price = 100.0 + (s_idx % 50) * 10.0
        has_split = sym in split_symbols
        for d_idx, d in enumerate(dates):
            if has_split and d_idx == 1000:
                prev_c = base_price * 0.5
                c = base_price * 0.5
            else:
                prev_c = base_price
                c = base_price

            records.append(
                (
                    sym,
                    d.isoformat(),
                    float(base_price),
                    float(base_price * 1.01),
                    float(base_price * 0.99),
                    float(c),
                    float(prev_c),
                    10000,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )

    cursor.executemany(
        """
        INSERT INTO daily_prices (
            symbol, date, open, high, low, close, prev_close, volume,
            adj_open, adj_high, adj_low, adj_close, adj_volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()

    with Session(engine) as session:
        # Measure full rebuild time
        t0 = time.perf_counter()
        updated_count = rebuild_adjusted(session=session, engine=engine)
        session.commit()
        time.perf_counter() - t0

        assert updated_count > 0, "Expected updated rows in full rebuild"

        # Measure incremental time for 500 new rows on day n_days + 1
        new_date = start_date + timedelta(days=n_days + 1)
        new_rows = [
            {
                "symbol": sym,
                "date": new_date,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "prev_close": 100.0,
                "volume": 10000,
            }
            for sym in symbols
        ]
        # Insert into DB first
        for r in new_rows:
            session.add(DailyPrice(**r))
        session.commit()

        new_df = pd.DataFrame(new_rows)
        t_inc_start = time.perf_counter()
        inc_count = adjust_new_rows(new_df, session=session, engine=engine)
        session.commit()
        time.perf_counter() - t_inc_start

        assert inc_count == n_symbols

        # Print metrics for PR reporting
