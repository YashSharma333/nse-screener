#!/usr/bin/env python3
"""Database Migration Wrapper (Deprecated).

This standalone migration script has been retired in favor of Alembic.
Running this script will execute `alembic upgrade head` to apply all migrations.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import setup_logging

setup_logging()
logger = logging.getLogger("migrate_add_index_name")


def main() -> None:
    logger.warning(
        "DEPRECATION NOTICE: 'scripts/migrate_add_index_name.py' is deprecated.\n"
        "Please use 'alembic upgrade head' directly for database migrations.\n"
        "Forwarding execution to 'alembic upgrade head'..."
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
