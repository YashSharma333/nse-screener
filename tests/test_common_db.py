"""Tests for src/common/db.py: use_session."""

import pytest

from src.common import db as common_db
from src.common.db import use_session


class FakeSession:
    """Records commit/rollback/close calls so tests can check them."""

    def __init__(self):
        self.calls = []

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def own_session(monkeypatch):
    """Make get_db hand out a FakeSession that behaves like the real get_db."""
    fake = FakeSession()

    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        try:
            yield fake
            fake.commit()
        except Exception:
            fake.rollback()
            raise
        finally:
            fake.close()

    monkeypatch.setattr(common_db, "get_db", fake_get_db)
    return fake


def test_given_session_is_yielded_and_left_alone():
    given = FakeSession()
    with use_session(given) as s:
        assert s is given
    assert given.calls == []


def test_given_session_is_not_rolled_back_on_error():
    given = FakeSession()
    with pytest.raises(ValueError, match="boom"), use_session(given):
        raise ValueError("boom")
    assert given.calls == []


def test_own_session_commits_and_closes(own_session):
    with use_session() as s:
        assert s is own_session
    assert own_session.calls == ["commit", "close"]


def test_own_session_rolls_back_on_error(own_session):
    with pytest.raises(ValueError, match="boom"), use_session(None):
        raise ValueError("boom")
    assert own_session.calls == ["rollback", "close"]
