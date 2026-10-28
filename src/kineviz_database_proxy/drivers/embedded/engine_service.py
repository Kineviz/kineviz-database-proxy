# -*- coding: utf-8 -*-
"""
From a store's header to a running engine, with one install path and two triggers.

The store says which on-disk format it is; the version map says which releases
write or read that format; this service installs one of them and opens the store
with it -- then records what actually happened, so the map is better next time
than the table it shipped with.

The two triggers are the reason the state lives here rather than inside the driver:

  - *Eager*. Dropping a file or saving a path fires an install straight away, and
    the form watches ``statuses()`` so the user sees a download rather than a
    stalled request.
  - *Lazy*. ``connect()`` asks for the same thing. A project restored from
    ``projects.json`` after a restart has never been through the form, and a query
    that lands mid-download must wait for the download rather than start a second
    one.

Both go through ``ensure``, which keeps one task per release and hands every caller
the same one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .pool import WORKER_POOL, EngineWorker, EngineWorkerError
from .pypi import PACKAGE_INDEX, PackageIndexError
from .runtime import (
    EngineInstallError,
    EngineRuntime,
    install_runtime,
    installed_runtime,
    remove_runtime,
)
from .store_probe import StoreFingerprint
from .wheelhouse import local_index
from .version_map import (
    VersionMap,
    is_release,
    load_version_map,
    newest,
    save_version_map,
    version_line,
)

STATUS_ABSENT = "absent"
STATUS_INSTALLING = "installing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

#: How many unknown releases discovery may download before giving up. Each is tens
#: of megabytes, so this is deliberately small: the store was almost certainly
#: written by something published just after this build, which is the first probe.
DISCOVERY_BUDGET = 3


@dataclass
class EngineStatus:
    """What the UI polls while an engine is being fetched."""

    engine: str
    version: str
    status: str = STATUS_ABSENT
    detail: str = ""
    error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)

    def to_json(self) -> Dict[str, object]:
        return {
            "engine": self.engine,
            "version": self.version,
            "status": self.status,
            "detail": self.detail,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class EngineResolutionError(RuntimeError):
    """No engine build could be found or installed for this store."""


class EngineService:
    """Version resolution, installs, and the learned map, for the whole process."""

    def __init__(self, version_map: Optional[VersionMap] = None):
        self._map = version_map if version_map is not None else load_version_map()
        self._statuses: Dict[Tuple[str, str], EngineStatus] = {}
        self._tasks: Dict[Tuple[str, str], "asyncio.Future[EngineRuntime]"] = {}
        self._lock = asyncio.Lock()

    # -- state the API reports ------------------------------------------

    @property
    def version_map(self) -> VersionMap:
        return self._map

    def status(self, engine: str, version: str) -> EngineStatus:
        key = (engine, version)
        state = self._statuses.get(key)
        if state is None:
            state = EngineStatus(
                engine=engine,
                version=version,
                status=STATUS_READY if installed_runtime(engine, version) else STATUS_ABSENT,
            )
            self._statuses[key] = state
        elif state.status == STATUS_ABSENT and installed_runtime(engine, version):
            state.status = STATUS_READY
        return state

    def statuses(self) -> List[EngineStatus]:
        return sorted(self._statuses.values(), key=lambda s: (s.engine, s.version))

    def _set(self, engine: str, version: str, status: str, detail: str = "", error: Optional[str] = None) -> EngineStatus:
        state = self.status(engine, version)
        state.status = status
        state.detail = detail
        state.error = error
        state.updated_at = time.time()
        return state

    # -- releases -------------------------------------------------------

    async def available_releases(self, engine: str) -> Optional[List[str]]:
        """
        What could be installed for this engine, or None when it cannot be reached.

        None rather than an empty list on purpose: "I could not ask" and "there are
        none" lead to different behaviour, and treating an offline proxy as the
        second would refuse a store it already has the engine for.

        A wheel built on this machine is as available as a published one, so the
        wheelhouse is added to the answer -- but only alongside an index that
        replied. Returning a wheelhouse of one file as *the* list while PyPI is
        unreachable would narrow an offline proxy down to that file and hide the
        releases it already has installed.
        """
        try:
            index = await PACKAGE_INDEX.get(engine)
        except PackageIndexError:
            return None
        versions = [v for v in index.versions if is_release(v)]
        known = set(versions)
        versions.extend(
            v for v in local_index(engine).versions if is_release(v) and v not in known
        )
        return versions

    async def candidates(
        self, engine: str, storage_version: int, pin: Optional[str] = None
    ) -> List[str]:
        """Releases to try for this store, best first, honouring a project's pin."""
        available = await self.available_releases(engine)
        if pin:
            pinned = self._resolve_pin(engine, pin, available)
            if pinned:
                return [pinned]
        ordered = self._map.candidates(engine, storage_version, available=available)
        if ordered:
            return ordered
        # Nothing known writes or reads this format. If it is newer than anything
        # this build was taught, it is worth finding out what does.
        return await self._discover(engine, storage_version, available)

    def local_candidates(
        self, engine: str, storage_version: int, pin: Optional[str] = None
    ) -> List[str]:
        """
        ``candidates`` without the network, for callers that answer per file.

        The store library names an engine release for every row it lists, and a
        listing is not the place to reach PyPI or to install a build on a hunch --
        a folder of thirty stores would become thirty round trips. What the map
        already knows is enough to say "this one is covered"; a store whose format
        is genuinely new still resolves properly when it is configured.
        """
        if pin:
            pinned = self._resolve_pin(engine, pin, None)
            if pinned:
                return [pinned]
        return self._map.candidates(engine, storage_version)

    def _resolve_pin(
        self, engine: str, pin: str, available: Optional[Sequence[str]]
    ) -> Optional[str]:
        """
        A project's ``engine_version``, which may name a line rather than a release.

        ``0.19`` means "the newest 0.19.x", which is how a user thinks about a pin
        and what keeps one from freezing a project onto a patch release forever.
        """
        text = str(pin).strip()
        if not text:
            return None
        pool = list(available) if available is not None else list(self._map.records(engine).keys())
        if text in pool:
            return text
        matching = [
            v for v in pool if is_release(v) and (version_line(v) == text or v.startswith(text + "."))
        ]
        chosen = newest(matching)
        if chosen:
            return chosen
        # An exact version nobody lists: trust the user, let the install report it.
        return text if is_release(text) else None

    async def _discover(
        self, engine: str, storage_version: int, available: Optional[Sequence[str]]
    ) -> List[str]:
        """
        Install and interrogate releases this build has never heard of.

        Only ever forward: a format *older* than everything known is not going to be
        explained by a newer release, and downloading a pile of wheels to prove that
        would be worse than the clear error the caller gets instead.
        """
        if available is None:
            return []
        known = self._map.known_formats(engine)
        if known and storage_version <= max(known):
            return []

        learned = False
        try:
            for version in self._map.discovery_candidates(engine, available, limit=DISCOVERY_BUDGET):
                self._set(engine, version, STATUS_INSTALLING, "probing an unrecognised store format")
                try:
                    runtime = await self.ensure(engine, version)
                    written = await self._interrogate(runtime)
                except (EngineInstallError, EngineWorkerError):
                    continue
                if written is None:
                    continue
                learned = True
                if written == storage_version:
                    break
        finally:
            if learned:
                save_version_map(self._map)

        return self._map.candidates(engine, storage_version, available=available)

    async def _interrogate(self, runtime: EngineRuntime) -> Optional[int]:
        """Start the build with no store open, ask what format it writes, record it."""
        worker = EngineWorker(runtime, None)
        try:
            await worker.start()
            info = await worker.info()
        finally:
            await worker.stop()
        written = info.get("storage_version")
        if not isinstance(written, int):
            return None
        self._map.learn_writes(runtime.engine, runtime.version, written)
        return written

    # -- installing -----------------------------------------------------

    async def ensure(self, engine: str, version: str) -> EngineRuntime:
        """
        The runtime for one release, installing it if it is missing.

        Single-flighted: the eager trigger and a query that arrives during the
        download await the same task.
        """
        key = (engine, version)
        async with self._lock:
            existing = installed_runtime(engine, version)
            if existing is not None:
                self._set(engine, version, STATUS_READY, "installed")
                return existing
            task = self._tasks.get(key)
            if task is None or task.done():
                task = asyncio.ensure_future(self._install(engine, version))
                self._tasks[key] = task
        return await task

    def start_install(self, engine: str, version: str) -> EngineStatus:
        """Kick off an install without waiting for it. The UI polls ``statuses()``."""
        key = (engine, version)
        if installed_runtime(engine, version):
            return self._set(engine, version, STATUS_READY, "installed")
        task = self._tasks.get(key)
        if task is None or task.done():
            self._set(engine, version, STATUS_INSTALLING, "queued")
            task = asyncio.ensure_future(self._install(engine, version))
            self._tasks[key] = task
            # Nobody may await this one; without a callback its exception would be
            # reported as "never retrieved" at interpreter shutdown instead of here.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return self.status(engine, version)

    async def _install(self, engine: str, version: str) -> EngineRuntime:
        self._set(engine, version, STATUS_INSTALLING, "starting")

        def progress(line: str) -> None:
            state = self._statuses.get((engine, version))
            if state is not None:
                state.detail = line[:200]
                state.updated_at = time.time()

        try:
            runtime = await install_runtime(engine, version, on_progress=progress)
        except Exception as exc:
            self._set(engine, version, STATUS_FAILED, "", str(exc))
            raise
        self._set(engine, version, STATUS_READY, "installed")
        return runtime

    # -- opening a store ------------------------------------------------

    async def open_store(
        self,
        fingerprint: StoreFingerprint,
        read_only: bool = True,
        pin: Optional[str] = None,
    ) -> Tuple[EngineRuntime, EngineWorker]:
        """
        A worker with this store open, on a build that can actually read it.

        Candidates are tried in order and the outcome is recorded either way: a build
        that opened the store is remembered as able to read that format, and one that
        refused it is un-remembered, so a guessed fall-forward is only ever wrong
        once per proxy.
        """
        engine = fingerprint.engine
        versions = await self.candidates(engine, fingerprint.storage_version, pin)
        if not versions:
            raise EngineResolutionError(
                f"No {engine} release is known to read storage version "
                f"{fingerprint.storage_version} ({fingerprint.header_path}). "
                f"The proxy knows formats "
                f"{sorted(self._map.known_formats(engine)) or 'none'}."
            )

        last_error: Optional[Exception] = None
        for version in versions:
            try:
                runtime = await self.ensure(engine, version)
            except Exception as exc:
                last_error = exc
                continue

            try:
                worker = await WORKER_POOL.acquire(
                    runtime, str(fingerprint.path), read_only
                )
            except EngineWorkerError as exc:
                last_error = exc
                # It could not open this store. Drop the claim that it can, so the
                # next request goes straight to the next candidate.
                if self._map.forget(engine, version, fingerprint.storage_version):
                    save_version_map(self._map)
                continue

            await self._learn_from(worker, fingerprint)
            return runtime, worker

        raise EngineResolutionError(
            f"Could not open {fingerprint.path} with any of {', '.join(versions)}: {last_error}"
        )

    async def _learn_from(self, worker: EngineWorker, fingerprint: StoreFingerprint) -> None:
        """Record that this build reads this format, and what it writes, exactly once."""
        engine = worker.runtime.engine
        version = worker.runtime.version
        record = self._map.records(engine).get(version)
        knows_writes = record is not None and record.writes is not None
        knows_reads = record is not None and fingerprint.storage_version in record.reads
        if knows_writes and knows_reads:
            return

        changed = self._map.learn_reads(engine, version, fingerprint.storage_version)
        if not knows_writes:
            try:
                info = await worker.info()
            except EngineWorkerError:
                info = {}
            written = info.get("storage_version")
            if isinstance(written, int):
                changed = self._map.learn_writes(engine, version, written) or changed
        if changed:
            save_version_map(self._map)

    # -- maintenance ----------------------------------------------------

    async def uninstall(self, engine: str, version: str) -> bool:
        """Remove an installed release. Used by the admin surface, not by a query."""
        removed = await asyncio.get_event_loop().run_in_executor(
            None, remove_runtime, engine, version
        )
        if removed:
            self._set(engine, version, STATUS_ABSENT, "removed")
        return removed


#: Process-wide: installs, the learned map and the in-flight tasks are all shared,
#: and drivers are built per request.
ENGINE_SERVICE = EngineService()
