"""
Which engine spelling serves a database type, and the refusal when none does.

Split out of ``engine.py``. The engine package is imported on first use so this
module never depends on it at import time, and a type with no case is named
instead of guessed at."""

from __future__ import annotations

from typing import Dict, Tuple

from .engine_spelling import EngineSpelling


class UnknownEngine(ValueError):
    """Raised for a database type no engine spelling is registered for.

    Refusing is the point. The client's proxy tables fall through to the bolt family
    when a backend has no case there, and a wrong-but-plausible dialect is what gave
    a Kuzu project ``ID(n) IN ["<Label>:<key>"]`` against an ``INTERNAL_ID`` column.
    A second copy of that mistake belongs nowhere near this registry.
    """


_REGISTRY: Dict[str, EngineSpelling] = {}
_LOADED = False


def register(spelling: EngineSpelling) -> EngineSpelling:
    """Add one engine spelling under its own ``name``."""
    _REGISTRY[spelling.name] = spelling
    return spelling


def engines_for(database_type: str) -> EngineSpelling:
    """The spelling for a database type, or :class:`UnknownEngine`.

    The engine package is imported on first use so this module never depends on it
    at import time, and a type with no case is named instead of guessing.
    """
    _ensure_loaded()
    key = str(database_type or "").strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise UnknownEngine(
            f"no relational engine spelling is registered for {database_type!r}. "
            "Registered: " + ", ".join(registered_engines()) + "."
        ) from None


def registered_engines() -> Tuple[str, ...]:
    _ensure_loaded()
    return tuple(sorted(_REGISTRY))


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import engines  # noqa: F401  (importing the package registers every engine)
