# -*- coding: utf-8 -*-
"""
Storage version to engine release, learned rather than hard-coded.

A store's header says which on-disk format it was written in. That number is what
decides which engine build can open it, and the mapping from one to the other is
many-to-one: every Kuzu 0.11.x writes format 39; Ladybug 0.12 through 0.16 all
write 40. Nothing in the file names a release.

So the map is keyed on the **storage version**, not on a release line, and the
release to install is simply the newest one known to write that format. That is
what "ignore the patch version" means here, and it falls out of the shape rather
than needing a rule: 0.19.0 and 0.19.1 both write 43, so asking for 43 gets
whichever of them is newest -- and it will pick up 0.19.2 the day it is published,
without this file changing.

Three layers answer a lookup, in order:

  1. **Seed** -- transcribed from each project's own ``storage_version_info.h``, so a
     cold proxy with no network resolves everything that exists today.
  2. **Learned** -- what the proxy observed by loading an engine and reading
     ``mod.storage_version``, or by successfully opening a store with it. Written to
     disk, so it survives a restart.
  3. **Discovery** -- for a format no layer knows, ``discovery_candidates`` narrows
     PyPI's release list to the few worth actually installing and probing.

The two families differ in one way that matters here. Kuzu refuses any format but
its own, so a Kuzu lookup is exact. Ladybug carries ``canReadStorageVersion`` and
accepts a range below itself, so a Ladybug lookup may fall forward to the nearest
newer release -- but only after the exact matches, and only as a guess that the
learned layer will confirm or drop once an open is actually attempted.

Pure: no network, no subprocess, no filesystem beyond the learned-map file. The
tests read it directly.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .store_probe import KUZU, LADYBUG, LATTICEDB

#: A release the proxy can reason about: digits and dots only. Anything else --
#: ``0.17.0.dev20260520`` and the rest of PyPI's pre-releases -- is skipped, because
#: a dev build's storage version is not a promise about anything.
_RELEASE_RE = re.compile(r"^\d+(\.\d+)*$")


def is_release(version: str) -> bool:
    """Whether a PyPI version string names a real release."""
    return bool(_RELEASE_RE.match(str(version).strip()))


def parse_version(version: str) -> Tuple[int, ...]:
    """``"0.11.3"`` -> ``(0, 11, 3)``. Non-releases sort below everything."""
    if not is_release(version):
        return ()
    return tuple(int(part) for part in str(version).strip().split("."))


def version_line(version: str) -> str:
    """
    The first two components, e.g. ``"0.19.1"`` -> ``"0.19"``.

    Used for two things only: telling a user which line they are on, and thinning
    the discovery probe list so an unknown format costs a couple of installs rather
    than one per patch. It is deliberately *not* how releases are matched to a
    storage version -- Kuzu's ancient ``0.0.x`` line would collapse formats 17
    through 24 into one bucket if it were.
    """
    parts = str(version).strip().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(version).strip()


def newest(versions: Iterable[str]) -> Optional[str]:
    """The highest release in a bag of version strings, or None if it holds none."""
    releases = [v for v in versions if is_release(v)]
    if not releases:
        return None
    return max(releases, key=parse_version)


# ---------------------------------------------------------------------------
# Seed: transcribed from storage_version_info.h in each project
# ---------------------------------------------------------------------------

#: kuzudb/kuzu, ``src/include/storage/storage_version_info.h`` at v0.11.3.
_KUZU_SEED: Dict[str, int] = {
    "0.11.3": 39, "0.11.2": 39, "0.11.1": 39, "0.11.0": 39,
    "0.10.0": 38,
    "0.9.0": 37,
    "0.8.0": 36,
    "0.7.1.1": 35, "0.7.0": 34,
    "0.6.0.6": 33, "0.6.0.5": 32, "0.6.0.2": 31, "0.6.0.1": 31,
    "0.6.0": 28, "0.5.0": 28,
    "0.4.2": 27, "0.4.1": 27, "0.4.0": 27,
    "0.3.2": 26, "0.3.1": 26, "0.3.0": 26,
    "0.2.1": 25, "0.2.0": 25,
    "0.1.0": 24, "0.0.12.3": 24, "0.0.12.2": 24, "0.0.12.1": 24,
    "0.0.12": 23, "0.0.11": 23, "0.0.10": 23, "0.0.9": 23,
    "0.0.8": 17, "0.0.7": 15, "0.0.6": 9, "0.0.5": 8, "0.0.4": 7, "0.0.3": 1,
}

#: LadybugDB/ladybug, same file on ``main``. Ladybug restarted numbering at 0.12,
#: continuing Kuzu's storage versions from 40 -- which is why the format number
#: alone is enough to tell the two families apart even without the magic bytes.
_LADYBUG_SEED: Dict[str, int] = {
    "0.12.0": 40, "0.12.2": 40, "0.13.0": 40, "0.13.1": 40,
    "0.14.0": 40, "0.14.1": 40,
    "0.15.0": 40, "0.15.1": 40, "0.15.2": 40, "0.15.3": 40, "0.15.4": 40,
    "0.16.0": 40, "0.16.1": 40,
    "0.17.0": 41, "0.17.1": 41,
    "0.18.0": 42, "0.18.1": 42,
    "0.19.0": 43, "0.19.1": 43,
    "0.20.0": 47,
}

#: jeffhajewski/latticedb. Unlike the other two this is not transcribed from a
#: header in the project: LatticeDB publishes no storage-version table, so the
#: numbers below were **measured** -- each release installed, a store created, and
#: its first bytes read, on 2026-08-28.
#:
#: Measured directly: 0.2.1, 0.3.0, 0.4.2, 0.5.0, 0.8.1, 0.8.7, 0.9.0, 0.9.6,
#: 0.10.0, 0.11.1, 0.12.0, 0.13.0, 0.14.0. The rest are interpolated, which is sound
#: only because each gap is bracketed by two measured releases that agree and the
#: format number never goes down: 0.8.2 sits between 0.8.1 and 0.8.7, both format 2.
#:
#: 0.2.0 is left out on purpose. It is the one release published as
#: ``py3-none-any`` -- a pure-Python wheel carrying no ``liblattice`` at all -- so
#: installing it could never produce a working engine.
_LATTICEDB_SEED: Dict[str, int] = {
    "0.2.1": 1, "0.3.0": 1, "0.4.0": 1, "0.4.2": 1,
    "0.5.0": 2,
    "0.8.1": 2, "0.8.2": 2, "0.8.3": 2, "0.8.4": 2, "0.8.5": 2, "0.8.6": 2, "0.8.7": 2,
    "0.9.0": 3, "0.9.2": 3, "0.9.3": 3, "0.9.4": 3, "0.9.5": 3, "0.9.6": 3,
    "0.10.0": 3,
    "0.11.0": 3, "0.11.1": 3,
    "0.12.0": 3, "0.13.0": 3, "0.14.0": 3,
}

SEED_RELEASES: Dict[str, Dict[str, int]] = {
    KUZU: dict(_KUZU_SEED),
    LADYBUG: dict(_LADYBUG_SEED),
    LATTICEDB: dict(_LATTICEDB_SEED),
}

#: Ladybug's ``canReadStorageVersion`` accepts formats below its own; Kuzu compares
#: for equality and throws otherwise. LatticeDB behaves like Ladybug -- 0.14.0 opens
#: stores in formats 1, 2 and 3, while 0.8.7 and 0.4.2 each refuse everything but
#: their own with a bare ``LatticeIOError`` -- so a newer build is a real fallback
#: for it too. All three were checked by opening actual stores, not read off a
#: changelog.
READS_OLDER_FORMATS = {KUZU: False, LADYBUG: True, LATTICEDB: True}


@dataclass
class EngineRecord:
    """One release, and what it is known to do with on-disk formats."""

    engine: str
    version: str
    #: The format this release writes. None when only its read support was observed.
    writes: Optional[int] = None
    #: Formats it has actually opened, which is stronger evidence than the seed.
    reads: Set[int] = field(default_factory=set)

    def to_json(self) -> Dict[str, object]:
        return {"writes": self.writes, "reads": sorted(self.reads)}


class VersionMap:
    """
    The seed and the learned layer, merged.

    Not thread-safe on purpose: it is mutated from the install path, which is
    already single-flighted per release, and read everywhere else.
    """

    def __init__(self, seed: Optional[Dict[str, Dict[str, int]]] = None):
        self._records: Dict[str, Dict[str, EngineRecord]] = {}
        for engine, releases in (seed if seed is not None else SEED_RELEASES).items():
            for version, storage_version in releases.items():
                self._record(engine, version).writes = storage_version

    # -- state ------------------------------------------------------------

    def _record(self, engine: str, version: str) -> EngineRecord:
        bucket = self._records.setdefault(engine, {})
        record = bucket.get(version)
        if record is None:
            record = EngineRecord(engine=engine, version=version)
            bucket[version] = record
        return record

    def records(self, engine: str) -> Dict[str, EngineRecord]:
        return dict(self._records.get(engine, {}))

    def writes_of(self, engine: str, version: str) -> Optional[int]:
        record = self._records.get(engine, {}).get(version)
        return record.writes if record else None

    def known_formats(self, engine: str) -> Set[int]:
        return {
            record.writes
            for record in self._records.get(engine, {}).values()
            if record.writes is not None
        }

    def newest_known_release(self, engine: str) -> Optional[str]:
        return newest(self._records.get(engine, {}).keys())

    # -- learning ---------------------------------------------------------

    def learn_writes(self, engine: str, version: str, storage_version: int) -> bool:
        """
        Record what a release reports as its own format, read from
        ``mod.storage_version`` after it was loaded. Returns whether anything changed,
        so the caller only rewrites the file when it has to.
        """
        record = self._record(engine, version)
        changed = record.writes != storage_version or storage_version not in record.reads
        record.writes = storage_version
        record.reads.add(storage_version)
        return changed

    def learn_reads(self, engine: str, version: str, storage_version: int) -> bool:
        """Record that this release actually opened a store in that format."""
        record = self._record(engine, version)
        if storage_version in record.reads:
            return False
        record.reads.add(storage_version)
        return True

    def forget(self, engine: str, version: str, storage_version: int) -> bool:
        """Record that this release could *not* open that format, so it stops being offered."""
        record = self._records.get(engine, {}).get(version)
        if record is None or storage_version not in record.reads:
            return False
        record.reads.discard(storage_version)
        return True

    # -- resolution -------------------------------------------------------

    def candidates(
        self,
        engine: str,
        storage_version: int,
        available: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """
        Releases that can open this format, best first.

        ``available`` is the release list from PyPI when there is one; without it the
        answer is drawn from what the map already knows, which is what keeps an
        offline proxy working.

        The order is by strength of evidence, not by recency:

          1. releases *observed* to open this exact format;
          2. releases that *write* it, newest first -- so 43 picks 0.19.1 over
             0.19.0, and would pick 0.19.2 the day it exists;
          3. for Ladybug only, releases writing a *newer* format, nearest first,
             since ``canReadStorageVersion`` looks backwards from where it stands.

        Kuzu never reaches step 3: it compares formats for equality, and letting it
        try a newer build turns a clear refusal into a ``UnicodeDecodeError``.
        """
        bucket = self._records.get(engine, {})
        allowed = self._allowed(engine, available)

        observed = [v for v, r in bucket.items() if storage_version in r.reads]
        writers = [v for v, r in bucket.items() if r.writes == storage_version]
        forward: List[str] = []
        if READS_OLDER_FORMATS.get(engine, False):
            forward = [
                v
                for v, r in bucket.items()
                if r.writes is not None and r.writes > storage_version
            ]

        ordered: List[str] = []
        ordered.extend(sorted(observed, key=parse_version, reverse=True))
        ordered.extend(sorted(writers, key=parse_version, reverse=True))
        # Nearest format above first, since the closest release is the least
        # behavioural drift from the one that wrote the store -- and within a format,
        # the newest patch, the same way the exact matches are ordered. Two stable
        # sorts rather than one key, because the two directions disagree.
        forward.sort(key=parse_version, reverse=True)
        forward.sort(key=lambda v: bucket[v].writes)
        ordered.extend(forward)

        result: List[str] = []
        for version in ordered:
            if version in result:
                continue
            if allowed is not None and version not in allowed:
                continue
            result.append(version)
        return result

    def discovery_candidates(
        self,
        engine: str,
        available: Sequence[str],
        limit: int = 6,
    ) -> List[str]:
        """
        Releases worth installing to find out what format they write.

        Only releases newer than anything the map knows, thinned to the newest patch
        of each line and taken newest-first: a store the proxy cannot place was
        almost certainly written by something published after this code was. The
        limit is there so an unknown format costs a bounded number of downloads
        rather than one per release ever published.
        """
        known = set(self._records.get(engine, {}).keys())
        floor = parse_version(self.newest_known_release(engine) or "0")

        per_line: Dict[str, str] = {}
        for version in available:
            if not is_release(version) or version in known:
                continue
            if parse_version(version) <= floor:
                continue
            line = version_line(version)
            if line not in per_line or parse_version(version) > parse_version(per_line[line]):
                per_line[line] = version

        return sorted(per_line.values(), key=parse_version, reverse=True)[:limit]

    def _allowed(
        self, engine: str, available: Optional[Sequence[str]]
    ) -> Optional[Set[str]]:
        if available is None:
            return None
        return {v for v in available if is_release(v)}

    # -- persistence ------------------------------------------------------

    def learned_json(self) -> Dict[str, Dict[str, Dict[str, object]]]:
        """
        Only what was observed, never the seed.

        Writing the seed back out would freeze this release's table into a user's
        config file, where a later proxy could no longer correct it.
        """
        out: Dict[str, Dict[str, Dict[str, object]]] = {}
        for engine, bucket in self._records.items():
            seeded = SEED_RELEASES.get(engine, {})
            for version, record in bucket.items():
                seed_writes = seeded.get(version)
                learned_reads = record.reads - ({seed_writes} if seed_writes else set())
                if record.writes == seed_writes and not learned_reads:
                    continue
                out.setdefault(engine, {})[version] = record.to_json()
        return out

    def merge_learned(self, data: Dict[str, Dict[str, Dict[str, object]]]) -> None:
        """Fold a learned-map file back in, ignoring anything malformed in it."""
        if not isinstance(data, dict):
            return
        for engine, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            for version, entry in bucket.items():
                if not isinstance(entry, dict) or not is_release(version):
                    continue
                record = self._record(str(engine), str(version))
                writes = entry.get("writes")
                if isinstance(writes, int):
                    record.writes = writes
                reads = entry.get("reads")
                if isinstance(reads, list):
                    record.reads.update(int(r) for r in reads if isinstance(r, int))


# ---------------------------------------------------------------------------
# The learned map on disk
# ---------------------------------------------------------------------------

DEFAULT_LEARNED_PATH = Path("config") / "engine_versions.json"


def learned_map_path() -> Path:
    """Where the learned layer lives; ``GRAPHXR_PROXY_ENGINE_MAP`` overrides it."""
    override = os.getenv("GRAPHXR_PROXY_ENGINE_MAP")
    return Path(override).expanduser() if override else DEFAULT_LEARNED_PATH


def load_version_map(path: Optional[Path] = None) -> VersionMap:
    """The seed with the learned file folded in. A missing or broken file is not fatal."""
    version_map = VersionMap()
    target = path or learned_map_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            version_map.merge_learned(json.load(handle))
    except (OSError, ValueError):
        pass
    return version_map


def save_version_map(version_map: VersionMap, path: Optional[Path] = None) -> None:
    """Write the learned layer. Best effort: a read-only config dir must not break a query."""
    target = path or learned_map_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(version_map.learned_json(), handle, indent=2, sort_keys=True)
    except OSError:
        pass
