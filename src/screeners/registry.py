"""Pluggable Screener Registry
===========================
Provides registration, lookup, and discovery for systematic screeners.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from src.screeners.base import Screener

logger = logging.getLogger("src.screeners.registry")

_REGISTRY: dict[str, type[Screener]] = {}

T = TypeVar("T", bound=type[Screener])


def register_screener(key: str | None = None) -> Callable[[T], T]:
    """Decorator to register a Screener subclass into the global registry."""

    def decorator(cls: T) -> T:
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
    """Retrieve and instantiate a registered screener by key."""
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(f"Unknown screener '{key}'. Available screeners: {available}")
    screener_cls = _REGISTRY[key]
    return screener_cls(**params)


def list_screeners(
    category: str | None = None,
    status: Literal["core", "experimental"] | None = None,
) -> list[type[Screener]]:
    """List registered screener classes, optionally filtered by category and/or status."""
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
    """List all registered screener keys, optionally filtered by category and/or status."""
    screeners = list_screeners(category=category, status=status)
    return [s.key for s in screeners]


def clear_registry() -> None:
    """Clear registered screeners (intended for unit tests)."""
    _REGISTRY.clear()
