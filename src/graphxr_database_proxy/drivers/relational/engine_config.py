"""
The slice of a project's configuration this seam reads, and how blank differs from absent.

Split out of ``engine.py``. :class:`ConnectConfig` is the whole of
``DatabaseConfig`` this seam may look at, which is why reading it is a set of
functions of that protocol rather than methods on a driver."""

from __future__ import annotations

import re
from typing import Mapping, Optional, Protocol, Sequence

from .engine_terms import ISOLATION_OPTION, SCHEMA_OPTION


class ConnectConfig(Protocol):
    """The slice of ``DatabaseConfig`` this seam reads.

    ``schema_name`` is the field the relational projects add to ``DatabaseConfig``;
    until it lands, ``options["schema"]`` carries the same meaning, and
    :func:`configured_schema` is the only place that knows the precedence — a config
    may supply either, neither, or both.
    """

    host: Optional[str]
    port: Optional[int]
    database_id: Optional[str]
    username: Optional[str]
    password: Optional[str]
    use_tls: bool
    options: Mapping[str, object]


def configured_schema(config: ConnectConfig, default: Optional[str]) -> Optional[str]:
    """The schema a name lives in: the field, then the option, then the engine default."""
    for candidate in (getattr(config, "schema_name", None), _option(config, SCHEMA_OPTION)):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return default


def option_value(config: ConnectConfig, name: str, default: object) -> object:
    """``config.options[name]``, or ``default`` when it is absent or blank."""
    value = _option(config, name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return value


def _option(config: ConnectConfig, name: str) -> object:
    options = getattr(config, "options", None) or {}
    try:
        return options.get(name)
    except AttributeError:
        return None


def timeout_seconds(config: ConnectConfig, name: str, default: int) -> int:
    """A positive timeout in seconds, or the default.

    Zero and negative are not passed through: to one client that means "no limit"
    and to another "fail immediately", and an unbounded read query is precisely the
    failure this option exists to prevent.
    """
    raw = option_value(config, name, None)
    if raw is None:
        return default
    try:
        seconds = int(float(str(raw)))
    except (TypeError, ValueError):
        return default
    return seconds if seconds > 0 else default


def pick_isolation(
    config: ConnectConfig,
    engine_name: str,
    accepted: Sequence[str],
    default: Optional[str],
) -> Optional[str]:
    """``options["isolation_level"]`` when this engine really has that level.

    An unknown name raises rather than being dropped. A person who set an isolation
    level believes it is in effect, and what they believe changes what the rows on
    their canvas mean.
    """
    raw = option_value(config, ISOLATION_OPTION, None)
    if raw is None:
        return default
    want = re.sub(r"[\s_]+", " ", str(raw).strip().upper())
    if want not in accepted:
        raise ValueError(
            f"{engine_name} has no isolation level {want!r}. It offers: "
            + ", ".join(sorted(accepted))
            + "."
        )
    return want
