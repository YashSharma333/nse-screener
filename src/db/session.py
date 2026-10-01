"""
Database Engine & Session Factory
==================================
Reads MySQL connection parameters from ``.env`` (via ``python-dotenv``),
constructs a connection-pooled SQLAlchemy engine using the ``mysql+pymysql``
dialect, and exposes:

* :func:`init_db` — run ``CREATE TABLE IF NOT EXISTS`` for every model.
* :func:`get_db`  — context manager that yields a ``Session`` and
  guarantees cleanup.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import setup_logging, PROJECT_ROOT
from src.db.models import Base

# ---------------------------------------------------------------------------
# Initialise centralized logging & load .env
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# Load .env from the project root (no-op if the file doesn't exist)
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Connection parameters (all read from environment / .env)
# ---------------------------------------------------------------------------
MYSQL_USER     = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT     = os.getenv("MYSQL_PORT", "3306")
MYSQL_DB       = os.getenv("MYSQL_DB", "nse_screener")

DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
)

# ---------------------------------------------------------------------------
# Engine (connection-pooled)
# ---------------------------------------------------------------------------
try:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,       # recycle stale connections every hour
        pool_pre_ping=True,      # verify connections before handing them out
    )
    logger.info(
        "SQLAlchemy engine created  (host=%s, db=%s, pool_size=10)",
        MYSQL_HOST, MYSQL_DB,
    )
except Exception as exc:
    logger.error("Failed to create SQLAlchemy engine: %s", exc)
    raise

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables defined in :mod:`src.db.models` if they don't exist.

    Safe to call repeatedly — ``CREATE TABLE IF NOT EXISTS`` is idempotent.
    Also ensures newly added columns such as ``index_name`` are migrated.
    """
    try:
        Base.metadata.create_all(bind=engine)
        try:
            with engine.connect() as conn:
                check_sql = text("""
                    SELECT COUNT(*)
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'daily_prices'
                      AND COLUMN_NAME = 'index_name'
                """)
                col_exists = conn.execute(check_sql).scalar()
                if col_exists == 0:
                    conn.execute(text("""
                        ALTER TABLE daily_prices
                        ADD COLUMN index_name VARCHAR(50) NULL,
                        ADD INDEX ix_daily_prices_index_name (index_name)
                    """))
                    conn.commit()
                    logger.info("Migrated daily_prices table: added index_name column.")
        except Exception as mig_err:
            logger.debug("Schema migration check skipped or failed: %s", mig_err)

        logger.info("Database tables initialised (create_all complete)")
    except Exception as exc:
        logger.error("Failed to initialise database tables: %s", exc)
        raise


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager that yields a SQLAlchemy ``Session``.

    Usage::

        with get_db() as session:
            session.execute(...)

    The session is committed on clean exit and rolled back on exception.
    ``session.close()`` is always called.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """Quick health-check: execute ``SELECT 1`` and return True on success."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health-check failed: %s", exc)
        return False