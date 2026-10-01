#!/usr/bin/env python3
"""
Database Migration Script: Add index_name column to daily_prices.
Safely checks and adds the index_name column and index if not present.
"""

import logging
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from config.settings import setup_logging
from src.db.session import engine, check_connection

setup_logging()
logger = logging.getLogger(__name__)


def migrate_schema() -> bool:
    """Safely add index_name column to daily_prices if it does not already exist."""
    if not check_connection():
        logger.error("Cannot connect to database. Ensure MySQL is running.")
        return False

    check_sql = text("""
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'daily_prices'
          AND COLUMN_NAME = 'index_name'
    """)

    alter_sql = text("""
        ALTER TABLE daily_prices
        ADD COLUMN index_name VARCHAR(50) NULL,
        ADD INDEX ix_daily_prices_index_name (index_name)
    """)

    try:
        with engine.connect() as conn:
            # Check if daily_prices table exists
            table_check = conn.execute(text("""
                SELECT COUNT(*)
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'daily_prices'
            """)).scalar()

            if table_check == 0:
                logger.info("Table daily_prices does not exist yet. Run init_db() to create it.")
                return True

            col_exists = conn.execute(check_sql).scalar()
            if col_exists == 0:
                logger.info("Adding index_name column to daily_prices...")
                conn.execute(alter_sql)
                conn.commit()
                logger.info("Successfully added index_name column to daily_prices.")
            else:
                logger.info("Column index_name already exists in daily_prices.")
            return True
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        return False


if __name__ == "__main__":
    migrate_schema()
