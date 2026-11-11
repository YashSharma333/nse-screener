"""Screener Registry
===================
A simple global dict that maps screener keys to their classes.
Use ``@register_screener()`` on a class to add it; use ``get_screener(key)``
to retrieve an instance anywhere in the codebase.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from src.screeners.base import Screener

logger = logging.getLogger("src.screeners.registry")

_REGISTRY: dict[str, type[Screener]] = {}


def register_screener(key: str | None = None):
    """Class decorator that registers a Screener subclass into the global registry.

    Usage:
        @register_screener()          # uses the class's own ``key`` attribute
        class MyScreener(Screener):
            key = "my_screener"
            ...

        @register_screener(key="alt_key")  # explicit override
        class MyScreener(Screener): ...
    """

    def decorator(cls: type[Screener]) -> type[Screener]:
        screener_key = key or getattr(cls, "key", "")
        if not screener_key:
            raise ValueError(
                f"Screener {cls.__name__} must define a non-empty 'key' attribute "
                "or specify a key in @register_screener."
            )
        if screener_key in _REGISTRY:
            reg_name = _REGISTRY[screener_key].__name__
            raise ValueError(
                f"Screener key '{screener_key}' already registered by {reg_name}. "
                f"Cannot overwrite with {cls.__name__}."
            )
        _REGISTRY[screener_key] = cls
        return cls

    return decorator


def get_screener(key: str, **params: Any) -> Screener:
    """Return an instantiated screener for the given key.

    Parameters
    ----------
    key : str
        The screener's registered key (e.g. ``"swing_volume"``).
    **params
        Optional parameter overrides passed to the screener's ``__init__``.
    """
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(f"Unknown screener '{key}'. Available screeners: {available}")
    return _REGISTRY[key](**params)


def list_screeners(
    category: str | None = None,
    status: Literal["core", "experimental"] | None = None,
) -> list[type[Screener]]:
    """Return registered screener classes, optionally filtered by category or status."""
    screeners = list(_REGISTRY.values())
    if category is not None:
        screeners = [s for s in screeners if s.category.lower() == category.lower()]
    if status is not None:
        screeners = [s for s in screeners if getattr(s, "status", "experimental") == status]
    return sorted(screeners, key=lambda s: (s.category, s.name))


def get_screener_keys(
    category: str | None = None,
    status: Literal["core", "experimental"] | None = None,
) -> list[str]:
    """Return all registered screener keys, optionally filtered."""
    return [s.key for s in list_screeners(category=category, status=status)]


def clear_registry() -> None:
    """Clear all registered screeners (for use in unit tests only)."""
    _REGISTRY.clear()
