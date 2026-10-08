# -*- coding: utf-8 -*-
"""
A short-lived cache for ``/graphSchema``.

The API builds a fresh driver per request, so nothing carries a schema over from
an earlier call: every ``/graphSchema``, every ``/sampleData``, and every
``/expand`` against a primary-key backend re-probes the store from a cold
connection. The probe is not free on any backend -- Neo4j scans for its property
types, Spanner and BigQuery read ``INFORMATION_SCHEMA`` -- and the answer changes
about as often as someone alters a table.

Three properties this has to have, in the order they bite:

  - **Bounded staleness.** The default TTL is a minute. A schema change shows up
    on the next reload rather than after a restart, and nothing has to be
    invalidated by hand.
  - **A config edit takes effect at once.** The key carries a digest of the
    connection config, so re-pointing a project at another database misses the
    cache rather than serving the previous database's schema. The digest is of
    the config, never stored alongside it -- credentials do not enter the cache.
  - **One probe per miss, not one per caller.** GraphXR opens a graph by asking
    for the schema from several panels at once. Without a per-key lock, a cold
    cache turns that into simultaneous identical probes; the lock makes the
    others wait for the first.

Set ``GRAPH_SCHEMA_CACHE_TTL_SECONDS=0`` to disable it entirely.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Dict, Optional, Tuple

from ..models.project import GraphSchema, Project

#: Long enough that opening a graph costs one probe rather than several, short
#: enough that someone who has just added a label sees it without restarting.
DEFAULT_TTL_SECONDS = 60.0

#: A hard ceiling on entries so a proxy with many projects cannot grow without
#: bound. Well above any realistic project count; eviction is a safety net.
MAX_ENTRIES = 256


def _ttl_from_env() -> float:
    raw = os.getenv("GRAPH_SCHEMA_CACHE_TTL_SECONDS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _max_entries_from_env() -> int:
    raw = os.getenv("GRAPH_SCHEMA_CACHE_MAX_ENTRIES")
    if raw is None or not raw.strip():
        return MAX_ENTRIES
    try:
        return max(1, int(raw))
    except ValueError:
        return MAX_ENTRIES


def cache_key(project: Project) -> str:
    """
    A project's identity *and* the connection it currently names.

    Editing a project keeps its id, so the id alone would serve the old
    database's schema until the entry expired. The config is digested rather
    than kept: it holds the password and the service-account key.
    """
    try:
        config = project.database_config.model_dump(mode="json")
    except Exception:  # pragma: no cover - a config that will not serialise
        config = {"type": str(getattr(project.database_config, "type", ""))}
    digest = hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"{project.id}:{digest}"


class GraphSchemaCache:
    """TTL cache of ``GraphSchema`` by project, with single-flight on a miss."""

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS, max_entries: int = MAX_ENTRIES):
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: Dict[str, Tuple[float, GraphSchema]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: str) -> Optional[GraphSchema]:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, schema = entry
        if time.monotonic() - stored_at > self.ttl:
            self._entries.pop(key, None)
            return None
        return schema

    def put(self, key: str, schema: GraphSchema) -> None:
        if not self.enabled:
            return
        self._entries[key] = (time.monotonic(), schema)
        if len(self._entries) > self.max_entries:
            self._evict()

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def invalidate_project(self, project_id: str) -> None:
        """Every entry for a project, whatever config digest it was stored under."""
        prefix = f"{project_id}:"
        for key in [key for key in self._entries if key.startswith(prefix)]:
            self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()

    def lock(self, key: str) -> asyncio.Lock:
        """The lock for one key, so concurrent misses probe once between them."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _evict(self) -> None:
        now = time.monotonic()
        for key, (stored_at, _) in list(self._entries.items()):
            if now - stored_at > self.ttl:
                self._entries.pop(key, None)
        while len(self._entries) > self.max_entries:
            oldest = min(self._entries, key=lambda key: self._entries[key][0])
            self._entries.pop(oldest, None)
        for key in [key for key in self._locks if key not in self._entries]:
            lock = self._locks[key]
            if not lock.locked():
                self._locks.pop(key, None)


#: The process-wide cache. Drivers are per request, so this cannot live on one.
GRAPH_SCHEMA_CACHE = GraphSchemaCache(ttl=_ttl_from_env(), max_entries=_max_entries_from_env())
