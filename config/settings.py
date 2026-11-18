"""
Centralized project settings and logging configuration.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
# Resolve the project root relative to this file (config/settings.py → ../)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOGS_DIR / "screener.log"

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
LOG_BACKUP_COUNT = 3  # keep 3 rotated copies


# ---------------------------------------------------------------------------
# Indicator Computation & Cache Window Configuration
# ---------------------------------------------------------------------------
INDICATOR_HISTORY_YEARS: int = int(os.getenv("INDICATOR_HISTORY_YEARS", "2"))
INDICATOR_WARMUP_BARS: int = int(os.getenv("INDICATOR_WARMUP_BARS", "300"))


def setup_logging() -> None:
    """Configure the root logger with a file handler and a stdout handler.

    • File handler  → logs/screener.log  (rotating, 5 MB × 3 backups)
    • Stream handler → sys.stdout

    Call this once at application startup (e.g. in ui/app.py).
    """
    # Ensure the logs directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    # Avoid adding duplicate handlers if setup_logging is called more than once
    if root_logger.handlers:
        return

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # --- Rotating file handler ---
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(LOG_FILE),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(formatter)

    # --- Stdout stream handler ---
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(LOG_LEVEL)
    stream_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# Corporate-action detection from price jumps (issue #48)
# ---------------------------------------------------------------------------
# NSE's PREV_CLOSE is NOT adjusted for splits and bonuses, so the engine looks for a
# big overnight price change instead. "Jump ratio" = previous close / new close.
# Clean ratios that real corporate actions produce: 2.0 = 1:1 bonus or 2:1 split,
# 10.0 = e.g. face value 10 to 1, 1.5 = 1:2 bonus, and so on.
SPLIT_CLEAN_RATIOS: tuple[float, ...] = (1.5, 5 / 3, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 20.0)
# A jump counts as a clean ratio when it is within this fraction of one (the market also
# moves on the ex-date, so the observed ratio is never exact).
SPLIT_SNAP_TOLERANCE: float = 0.06
# Only jumps at least this big are applied automatically. Daily price limits are 20 percent
# or less for most stocks, so a drop of about 31 percent or more is almost surely a split.
SPLIT_MIN_AUTO_RATIO: float = 1.45
# Smaller but still unusual jumps (down 20 percent and more) are only listed for review.
SPLIT_REVIEW_MIN_RATIO: float = 1.25
# If more than this share of symbols jump on one date, it is a data problem, not splits.
SPLIT_GUARD_FRACTION: float = 0.15
SPLIT_GUARD_MIN_SYMBOLS: int = 5
# The review report compares ex-date volume with the median of this many earlier days.
SPLIT_VOLUME_LOOKBACK_DAYS: int = 20
# Owner-approved corrections to the detected events (see load_adjustment_overrides).
ADJUSTMENT_OVERRIDES_FILE: Path = PROJECT_ROOT / "config" / "adjustment_overrides.csv"
