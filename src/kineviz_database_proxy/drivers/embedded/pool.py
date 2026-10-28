# -*- coding: utf-8 -*-
"""
The proxy's side of the worker protocol, and the handful of live workers.

A worker is a subprocess holding one engine build with one database open. Starting
one costs an interpreter start plus an engine import -- around a second -- so they
are kept alive between requests, which matters because the API builds a fresh
driver per call and would otherwise pay that on every panel GraphXR opens.

Two properties the pool has to have:

  - **One statement at a time per worker.** The protocol is a request/response pipe
    with no interleaving, and an embedded store is single-writer anyway, so there is
    nothing to gain from concurrency against one file and a desynchronised pipe to
    lose.
  - **A crash is an error, not a hang.** If the process dies -- an engine segfault on
    a corrupt store is a real possibility -- the pending request fails with whatever
    the process wrote to stderr, and the entry is dropped so the next call starts a
    fresh one.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

from .runtime import WORKER_SCRIPT, EngineRuntime

#: A worker that nobody has used for this long is shut down. Long enough to cover a
#: user reading a graph and clicking again, short enough that an idle proxy is not
#: holding engine processes and file handles open indefinitely.
DEFAULT_IDLE_SECONDS = 300.0

#: A ceiling on live engine processes, so a proxy with many embedded projects cannot
#: run the machine out of handles. Least-recently-used is evicted past it.
MAX_WORKERS = 8

#: Statement timeout. Generous: an analytical query over a large store is the point
#: of these engines, and the store is local, so a slow answer is not a dead one.
DEFAULT_REQUEST_TIMEOUT = 300.0

#: Starting the process and importing the engine.
STARTUP_TIMEOUT = 60.0

STDERR_TAIL_LINES = 12

#: Marks the start of a reply frame; must match ``worker.FRAME_PREFIX``.
FRAME_PREFIX = b"GXRPROXY "

#: The stream reader's per-line buffer. It bounds the *header* line and any noise the
#: engine prints, never a reply -- those are read by byte count. Generous so a chatty
#: engine cannot trip it, and small enough that garbage on stdout cannot grow without
#: bound.
STREAM_LIMIT = 1024 * 1024


class EngineWorkerError(RuntimeError):
    """The worker failed, died, or answered with the engine's own error."""


def _idle_seconds() -> float:
    raw = os.getenv("GRAPHXR_PROXY_ENGINE_IDLE_SECONDS")
    if not raw:
        return DEFAULT_IDLE_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_IDLE_SECONDS


class EngineWorker:
    """One engine subprocess, with one database open."""

    def __init__(self, runtime: EngineRuntime, path: Optional[str], read_only: bool = True):
        self.runtime = runtime
        #: None for a worker started only to ask the build what it is -- the probe
        #: that teaches the version map, which has no store to open yet.
        self.path = path
        self.read_only = read_only
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._stderr: Deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_task: Optional[asyncio.Task] = None
        self._next_id = 0
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self.alive:
            return
        self._process = await asyncio.create_subprocess_exec(
            str(self.runtime.python),
            "-u",
            str(WORKER_SCRIPT),
            self.runtime.engine,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.runtime.env(),
            cwd=str(WORKER_SCRIPT.parent),
            limit=STREAM_LIMIT,
        )
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())
        # Ping first: it is the import of the engine that is slow and that fails, and
        # a failure here reports the import rather than the store.
        await self._call("ping", timeout=STARTUP_TIMEOUT)
        if self.path:
            await self._call(
                "open", path=self.path, read_only=self.read_only, timeout=STARTUP_TIMEOUT
            )

    async def info(self) -> Dict[str, Any]:
        """What this build calls itself, and which storage format it writes."""
        return await self.request("info", timeout=STARTUP_TIMEOUT)

    async def stop(self) -> None:
        process, self._process = self._process, None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if process is None or process.returncode is not None:
            return
        try:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.write(b'{"op":"shutdown"}\n')
                await process.stdin.drain()
                process.stdin.close()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    async def _drain_stderr(self) -> None:
        """
        Keep the child's stderr moving.

        Not only for the error messages: a full stderr pipe blocks the writer, and a
        blocked engine process looks exactly like a slow query.

        Read in chunks and split here rather than iterated line by line. Line
        iteration goes through the same limit that broke reply reading, and a C++
        engine dumping a long single-line diagnostic would stop the drain -- which
        would show up as the engine hanging, not as a long log line.
        """
        process = self._process
        if process is None or process.stderr is None:
            return
        pending = b""
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self._stderr.append(line)
                if len(pending) > STREAM_LIMIT:
                    # A diagnostic with no newline in sight; keep the tail, drop the rest.
                    pending = pending[-4096:]
        except asyncio.CancelledError:
            return
        except Exception:
            return
        finally:
            line = pending.decode("utf-8", "replace").rstrip()
            if line:
                self._stderr.append(line)

    # -- protocol -------------------------------------------------------

    async def request(
        self, operation: str, timeout: float = DEFAULT_REQUEST_TIMEOUT, **payload: Any
    ) -> Dict[str, Any]:
        """One request/response exchange. Serialised: the pipe carries one at a time."""
        async with self._lock:
            if not self.alive:
                await self.start()
            self.last_used = time.monotonic()
            return await self._call(operation, timeout=timeout, **payload)

    async def _call(
        self, operation: str, timeout: float = DEFAULT_REQUEST_TIMEOUT, **payload: Any
    ) -> Dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise EngineWorkerError("engine worker is not running")

        self._next_id += 1
        request_id = self._next_id
        message = {"op": operation, "id": request_id}
        message.update(payload)

        try:
            process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()
            response = await asyncio.wait_for(self._read_response(process), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self.stop()
            raise EngineWorkerError(
                f"{self.runtime.engine} {self.runtime.version} did not answer "
                f"within {int(timeout)}s"
            ) from exc
        except EngineWorkerError:
            raise
        except Exception as exc:
            await self.stop()
            raise EngineWorkerError(f"engine worker failed: {exc}") from exc

        if not response.get("ok"):
            raise EngineWorkerError(str(response.get("error") or "engine reported an error"))
        return response

    async def _read_response(self, process: asyncio.subprocess.Process) -> Dict[str, Any]:
        """
        The next reply.

        A reply arrives as a short header line naming the payload's size, then the
        payload read off by byte count. That indirection is the whole point:
        ``readline()`` gives up at ``limit`` bytes with "Separator is not found, and
        chunk exceed the limit", and *every* real query result is past 64 KiB, so
        reading a reply as a line worked in the tests and failed on the first real
        graph. ``readexactly`` has no such ceiling.

        Anything that is neither a frame nor a JSON object carrying ``ok`` is skipped
        rather than treated as a reply: an engine that prints a banner or a
        deprecation notice on stdout would otherwise desynchronise every later
        exchange.
        """
        assert process.stdout is not None
        while True:
            try:
                raw = await process.stdout.readline()
            except ValueError:
                # A single line of noise longer than the limit. `readline` has already
                # dropped it from the buffer, so carrying on is safe -- and it cannot
                # have been one of ours, whose header lines are a few bytes.
                continue
            if not raw:
                raise await self._exited()

            line = raw.strip()
            if not line:
                continue

            if line.startswith(FRAME_PREFIX):
                try:
                    size = int(line[len(FRAME_PREFIX) :])
                except ValueError:
                    continue
                try:
                    payload = await process.stdout.readexactly(size)
                    await process.stdout.readline()  # the newline after the payload
                except asyncio.IncompleteReadError:
                    raise await self._exited()
                candidate = payload
            else:
                # A bare JSON line. Small replies and test doubles still arrive this
                # way, and accepting them costs nothing.
                candidate = line

            try:
                parsed = json.loads(candidate.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(parsed, dict) and "ok" in parsed:
                return parsed

    async def _exited(self) -> EngineWorkerError:
        """The error for a process that went away mid-exchange, carrying its stderr."""
        await self.stop()
        tail = self.stderr_tail()
        return EngineWorkerError(
            f"{self.runtime.engine} {self.runtime.version} exited unexpectedly"
            + (f":\n{tail}" if tail else "")
        )


WorkerKey = Tuple[str, str, str, bool]


class WorkerPool:
    """Live workers, keyed by engine build and open database."""

    def __init__(self, max_workers: int = MAX_WORKERS):
        self.max_workers = max_workers
        self._workers: Dict[WorkerKey, EngineWorker] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(runtime: EngineRuntime, path: str, read_only: bool) -> WorkerKey:
        return (runtime.engine, runtime.version, str(Path(path).resolve()), read_only)

    async def acquire(
        self, runtime: EngineRuntime, path: str, read_only: bool = True
    ) -> EngineWorker:
        key = self.key(runtime, path, read_only)
        async with self._lock:
            await self._sweep()
            worker = self._workers.get(key)
            if worker is not None and worker.alive:
                worker.last_used = time.monotonic()
                return worker
            if worker is not None:
                self._workers.pop(key, None)

            worker = EngineWorker(runtime, str(Path(path).resolve()), read_only)
            await worker.start()
            self._workers[key] = worker
            await self._evict_over_capacity()
            return worker

    async def release(self, runtime: EngineRuntime, path: str, read_only: bool = True) -> None:
        """Drop a worker deliberately -- after a config change, or a failed probe."""
        key = self.key(runtime, path, read_only)
        async with self._lock:
            worker = self._workers.pop(key, None)
        if worker is not None:
            await worker.stop()

    async def shutdown(self) -> None:
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            await worker.stop()

    async def _sweep(self) -> None:
        idle = _idle_seconds()
        if idle <= 0:
            return
        now = time.monotonic()
        expired = [
            key
            for key, worker in self._workers.items()
            if not worker.alive or now - worker.last_used > idle
        ]
        for key in expired:
            worker = self._workers.pop(key, None)
            if worker is not None:
                await worker.stop()

    async def _evict_over_capacity(self) -> None:
        while len(self._workers) > self.max_workers:
            oldest = min(self._workers, key=lambda key: self._workers[key].last_used)
            worker = self._workers.pop(oldest, None)
            if worker is not None:
                await worker.stop()


#: Process-wide, because drivers are built per request and workers must outlive them.
WORKER_POOL = WorkerPool()
