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
from collections.abc import Generator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import PROJECT_ROOT, setup_logging
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
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_DB = os.getenv("MYSQL_DB", "nse_screener")

DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

# ---------------------------------------------------------------------------
# Engine (connection-pooled)
# ---------------------------------------------------------------------------
try:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,  # recycle stale connections every hour
        pool_pre_ping=True,  # verify connections before handing them out
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info(
        "SQLAlchemy engine created  (host=%s, db=%s, pool_size=10)",
        MYSQL_HOST,
        MYSQL_DB,
    )
except (ImportError, Exception) as exc:
    logger.warning(
        "Could not initialize MySQL SQLAlchemy engine (%s) — DB operations will be disabled.", exc
    )
    engine = None
    SessionLocal = None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create any missing tables defined in :mod:`src.db.models` (``CREATE TABLE IF NOT EXISTS``).

    Safe to call repeatedly. It never alters or drops anything: schema changes go through
    Alembic migrations only.
    """
    if engine is None:
        logger.warning("Database init_db skipped: engine is not available.")
        return
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logger.warning(
            "Database tables could not be initialised (MySQL offline or unreachable): %s", exc
        )


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager that yields a SQLAlchemy ``Session``.

    Usage::

        with get_db() as session:
            session.execute(...)

    The session is committed on clean exit and rolled back on exception.
    ``session.close()`` is always called.
    """
    if SessionLocal is None:
        raise RuntimeError(
            "Database session factory uninitialized. Ensure pymysql & MySQL are configured."
        )
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
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health-check failed: %s", exc)
        return False
