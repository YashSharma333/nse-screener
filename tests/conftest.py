"""Global test configuration and fixtures for nse-screener."""

from __future__ import annotations

import datetime
import sqlite3

# Explicitly register sqlite3 date adapter and converter to address Python 3.12+ deprecation
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_converter("date", lambda b: datetime.date.fromisoformat(b.decode()))
