"""Tests for src/db/session.py."""

from __future__ import annotations

from unittest.mock import patch

from src.db.session import init_db


def test_init_db_graceful_on_operational_error() -> None:
    """init_db must log a warning and return gracefully when the DB is offline."""
    with patch(
        "src.db.session.Base.metadata.create_all", side_effect=Exception("Connection refused")
    ):
        init_db()  # must not raise
