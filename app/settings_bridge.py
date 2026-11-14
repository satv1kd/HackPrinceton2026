"""Temporary seam onto Task 1's configuration module.

Task 1 owns config loading (``.env`` -> a settings object). These modules were
written before it landed, so they read configuration through this shim, which
prefers, in order:

1. an attribute on ``app.config.settings``, if that module exists,
2. the process environment,
3. the caller's default.

**Delete this once Task 1 is merged** and import the real settings object
directly. The only thing that must stay true is the key names, which come
from the PRD's config list.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

_MISSING = object()


def _from_task1_config(name: str) -> Any:
    try:
        from app.config import get_settings
        settings = get_settings()
    except Exception:
        return _MISSING
    for attribute in (name, name.lower()):
        if hasattr(settings, attribute):
            return getattr(settings, attribute)
    getter = getattr(settings, "get", None)
    if callable(getter):
        value = getter(name, _MISSING)
        if value is not _MISSING:
            return value
    return _MISSING


def get(name: str, default: T, cast: Optional[Callable[[Any], T]] = None) -> T:
    """Read config value *name*, falling back to *default*."""
    value = _from_task1_config(name)
    if value is _MISSING:
        value = os.environ.get(name, _MISSING)
    if value is _MISSING:
        return default
    if cast is None:
        return value  # type: ignore[return-value]
    try:
        return cast(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"config value {name}={value!r} is not valid: {error}") from error


def get_bool(name: str, default: bool) -> bool:
    """Read a boolean config value, accepting the usual spellings."""
    value = get(name, _MISSING)  # type: ignore[arg-type]
    if value is _MISSING:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
