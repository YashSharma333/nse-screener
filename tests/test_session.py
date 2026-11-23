"""Tests for src/db/session.py."""

from __future__ import annotations

from src.db import session


def test_password_with_special_characters_survives_in_the_url(monkeypatch) -> None:
    """A password holding @ / : must not break the URL (an f-string would split it)."""
    monkeypatch.setenv("MYSQL_PASSWORD", "p@ss:w/rd")
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_PORT", "3307")

    url = session.build_database_url()

    assert url.password == "p@ss:w/rd"
    assert url.host == "db.example.com"
    assert url.port == 3307


def test_init_db_is_gone() -> None:
    """Schema changes go through Alembic only: no create_all helper may exist."""
    assert not hasattr(session, "init_db")
