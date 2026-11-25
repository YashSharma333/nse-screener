"""One helper for "use the session I was given, or open my own".

Many functions take an optional ``session`` argument. Before this helper each
one repeated ``if session is not None: ... else: with get_db() as s: ...``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from src.db.session import get_db


@contextmanager
def use_session(session: Session | None = None) -> Iterator[Session]:
    """Yield the given session untouched, or open one that commits and closes itself.

    Why: a function that receives a session must use it as given. The caller
    decides when to commit or roll back, so this helper never commits or closes
    a session it did not open.

    Example::

        with use_session(session) as s:
            s.execute(...)
    """
    if session is not None:
        yield session
        return
    with get_db() as own_session:
        yield own_session
