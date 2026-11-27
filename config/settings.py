"""
Centralized project settings and logging configuration.
"""

import logging
import logging.handlers
import os
import sys
from datetime import date
from pathlib import Path

from src.computation.indicators.resolver import ind

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
# Resolve the project root relative to this file (config/settings.py → ../)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOGS_DIR / "screener.log"

# ---------------------------------------------------------------------------
# Where output files live: ONE definition per folder (task D14)
# ---------------------------------------------------------------------------
# Code must read these as ``settings.REPORTS_DIR`` when a function runs (not copy them at
# import time). Then the tests can point every writer at a temporary folder by changing this
# one value, and no test can overwrite the owner's real reports.
# (Database backups are the only files outside the repo: ~/nse-backups, see
# scripts/backup_db.sh.)
REPORTS_DIR: Path = PROJECT_ROOT / "reports"
# Raw benchmark index closes and the benchmark's own list of non-trading days.
BENCHMARK_CACHE_DIR: Path = PROJECT_ROOT / "data" / "raw" / "benchmark_cache"
BENCHMARK_NON_TRADING_DAYS_FILE: Path = BENCHMARK_CACHE_DIR / "non_trading_days.json"
# Days where NSE has no Bhavcopy (holidays), learned from 404s by the price loader.
PRICE_NON_TRADING_DAYS_FILE: Path = (
    PROJECT_ROOT / "data" / "raw" / "bhavcopy_cache" / "non_trading_days.json"
)
# The indicator cache. Its sidecar file is always the same name with ".meta.json"
# (``cache_path.with_suffix(".meta.json")``). The builder (src/db/data_service.py) writes it;
# the dashboard only reads it (src/computation/cache_reader.py).
INDICATORS_CACHE_PATH: Path = PROJECT_ROOT / "data" / "processed" / "indicators.parquet"
# Index closes (index_code, date, close) written by the cache build, so the read-only UI and the
# condition engine can use any stored index without opening MySQL.
BENCHMARKS_CACHE_PATH: Path = PROJECT_ROOT / "data" / "processed" / "benchmarks.parquet"

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
# The warm-up is never shorter than this, even if the environment asks for fewer bars:
# the longest cached indicator (the 200-day average and its slope) needs about this many.
INDICATOR_MIN_WARMUP_BARS: int = 300
# The index every relative-strength indicator is measured against.
BENCHMARK_INDEX_CODE: str = "NIFTY 500"

# The columns every cache row starts with (the price columns, no indicators).
CACHE_BASE_COLUMNS: list[str] = [
    "symbol",
    "date",
    "series",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "market_cap",
]

# The ONLY indicators saved in the cache (owner list, CL-25). Each column is named by the
# resolver (``spec.column``), for example ``sma_close_50``. Everything else is computed when it
# is asked for (``cache_reader.load_with_indicators``). The two 252-day columns are the
# PRIOR-day window: today's close can never break a window that includes today.
PRECOMPUTED_INDICATORS = [
    ind("sma", length=20),
    ind("sma", length=50),
    ind("sma", length=200),
    ind("ema", length=9),
    ind("ema", length=10),
    ind("ema", length=20),
    ind("ema", length=50),
    ind("ema", length=200),
    ind("bollinger_bands", output="upper"),
    ind("bollinger_bands", output="lower"),
    ind("sma", source="volume", length=20),
    ind("sma", source="volume", length=50),
    ind("rsi", length=14),
    ind("window", source="high", length=252, stat="max", days_ago=1),
    ind("window", source="low", length=252, stat="min", days_ago=1),
]


def setup_logging() -> None:
    """Configure the root logger with a file handler and a stdout handler.

    • File handler  → logs/screener.log  (rotating, 5 MB × 3 backups)
    • Stream handler → sys.stdout

    Call this once at application startup (e.g. in ui/Dashboard.py).
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
# Unexplained price-jump report (prices are adjusted from OFFICIAL actions only, task CA2)
# ---------------------------------------------------------------------------
# "Jump ratio" = previous close / new close. An overnight move this big (down 20 percent
# or more, or the matching rise) that no applied corporate action explains is only LISTED
# for the owner in reports/adj_events_uncertain.csv. It never changes a price.
JUMP_REPORT_MIN_RATIO: float = 1.25
# If more than this share of symbols jump on one date, it is a data problem, not a corporate
# action, so that date is left out of the report. The check needs at least
# JUMP_REPORT_MIN_SYMBOLS symbols on the date to mean anything.
JUMP_REPORT_MAX_BAD_DAY_SHARE: float = 0.15
JUMP_REPORT_MIN_SYMBOLS: int = 5
# ---------------------------------------------------------------------------
# Official NSE corporate-actions reports (task CA1)
# ---------------------------------------------------------------------------
# Raw NSE responses and parsed snapshots are kept here (git-ignored, see .gitignore).
CORP_ACTIONS_DIR: Path = PROJECT_ROOT / "data" / "corporate_actions"
# NSE's website API. NSE also mirrors the site on nseindia.in, so the host can be switched
# with an environment variable if nseindia.com ever stops answering.
NSE_API_BASE: str = os.getenv("NSE_API_BASE", "https://www.nseindia.com")
NSE_CORP_ACTIONS_API_PATH: str = "/api/corporates-corporateActions"
# Static report files live on the same archive host as the Bhavcopy files.
NSE_ARCHIVES_BASE: str = "https://nsearchives.nseindia.com"
# NSE's API returned the full 10 years in one call (verified 18 Nov 2026). We still ask one
# calendar year at a time so a failed call only repeats one small piece.
CORP_ACTIONS_HISTORY_YEARS: int = 10
# Include actions NSE has already announced for the coming weeks (future ex-dates).
CORP_ACTIONS_FORWARD_DAYS: int = 90
# Stop before writing to the database when more than this share of split, bonus and
# consolidation actions cannot be turned into an exact factor. Real data today: about 0.3
# percent, so a jump means NSE changed its wording.
CORP_ACTIONS_MAX_UNPARSED_SHARE: float = 0.05

# Owner-approved corrections to the detected events (see load_adjustment_overrides).
ADJUSTMENT_OVERRIDES_FILE: Path = PROJECT_ROOT / "config" / "adjustment_overrides.csv"

# ---------------------------------------------------------------------------
# Official NSE file download settings (task D2)
# ---------------------------------------------------------------------------
# Official NSE hosts
NSE_WWW_HOST: str = "www.nseindia.com"
NSE_ARCHIVES_HOST: str = "nsearchives.nseindia.com"
# One timeout (seconds) for every NSE download, and the polite pause (seconds) after a
# downloaded Bhavcopy or PR-bundle file and after each benchmark day.
NSE_REQUEST_TIMEOUT_SECONDS: int = 30
PRICE_DOWNLOAD_PAUSE_SECONDS: float = 0.3
BENCHMARK_DOWNLOAD_PAUSE_SECONDS: float = 0.5
# The 52-week cross-check looks back this many calendar days (a year of history per date).
CROSSCHECK_WINDOW_DAYS: int = 365
# A cross-check comparison fails when our 52-week high or low is more than this many percent
# away from NSE's official number.
CROSSCHECK_TOLERANCE_PCT: float = 1.0

# Official file start dates:
# • 2016-11-21: earliest daily price history in scope
# • 2024-01-01: cut-over date to the new UDiFF Bhavcopy format
# • 2024-02-01: earliest PR bundle containing usable market cap files
NSE_HISTORY_START: date = date(2016, 11, 21)
NSE_UDIFF_START: date = date(2024, 1, 1)
FIRST_MCAP_DATE: date = date(2024, 2, 1)

# ---------------------------------------------------------------------------
# Bhavcopy parsing and raw cache settings (task D3 & D6)
# ---------------------------------------------------------------------------
# Regular equity trading series kept in daily_prices:
# • EQ: Normal rolling settlement equity shares
# • BE: Trade-for-trade equity shares (illiquid or under surveillance)
# • BZ: Surveillance/restricted trading category
# • SM: SME equity shares (normal trading)
# • ST: SME trade-for-trade equity shares
# All other series (BL block deals, N* debentures, P1 preference, W*, GB, GS, IV)
# are other instruments sharing a ticker symbol that would violate the (symbol, date) primary key.
REGULAR_SERIES: tuple[str, ...] = ("EQ", "BE", "BZ", "SM", "ST")

# Raw parsed Bhavcopy parquet cache (new full-universe folder; old EQ-only cache is deprecated)
BHAVCOPY_ALL_DIR: Path = PROJECT_ROOT / "data" / "raw" / "nse" / "bhavcopy_all"

# Raw parsed Market Cap parquet cache (task D6)
MCAP_DIR: Path = PROJECT_ROOT / "data" / "raw" / "nse" / "mcap"
