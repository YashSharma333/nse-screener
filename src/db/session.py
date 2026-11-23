"""
Database Engine & Session Factory
==================================
Reads MySQL connection parameters from ``.env`` (via ``python-dotenv``),
constructs a connection-pooled SQLAlchemy engine using the ``mysql+pymysql``
dialect, and exposes:

* :func:`build_database_url` — the connection URL, safe for any password.
* :func:`get_db`  — context manager that yields a ``Session`` and
  guarantees cleanup.

Tables are created only by Alembic migrations, never from here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import PROJECT_ROOT, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Load .env from the project root (no-op if the file doesn't exist)
load_dotenv(PROJECT_ROOT / ".env")


def build_database_url() -> URL:
    """Build the MySQL URL from the MYSQL_* environment variables.

    URL.create keeps every part separate, so a password with ``@ / :`` cannot break the
    URL (an f-string would split it at the wrong character).
    """
    return URL.create(
        drivername="mysql+pymysql",
        username=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DB", "nse_screener"),
    )


# create_engine does not connect yet, so a wrong host fails later at the first query.
# A real mistake (missing driver, bad port) raises here instead of being hidden.
engine = create_engine(
    build_database_url(),
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,  # recycle stale connections every hour
    pool_pre_ping=True,  # verify connections before handing them out
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
logger.info(
    "SQLAlchemy engine created  (host=%s, db=%s, pool_size=10)",
    engine.url.host,
    engine.url.database,
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
