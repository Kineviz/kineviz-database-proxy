# -*- coding: utf-8 -*-
"""
Putting one engine release on disk, and knowing how to run it.

Each ``(engine, version)`` gets its own directory under the engines root, holding
nothing but that release. That is not tidiness: two builds of these engines cannot
share a process at all -- importing ``kuzu`` after ``ladybug`` raises
``generic_type: type "Database" is already registered!``, because both pybind11
modules claim the same type names in one global registry -- so each build needs its
own interpreter process, and the cleanest way to give it one is its own install.

Two install shapes, in preference order:

  - **A uv-managed virtual environment.** Preferred, because uv can also *provide*
    the interpreter. This box runs CPython 3.14 and ``kuzu`` 0.11.3 ships no
    ``cp314`` wheel for Windows, so running the engine on the proxy's own
    interpreter is not always possible; uv downloads a 3.13 and the engine runs
    there while the proxy stays where it is.
  - **A ``--target`` install against the running interpreter.** The fallback when uv
    is absent. It only works when this interpreter has a wheel, and says so plainly
    when it does not, rather than letting pip spend five minutes failing to compile
    a C++ project.

Nothing here imports an engine. That happens only in the worker subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .pypi import (
    PACKAGE_INDEX,
    PackageIndex,
    PackageIndexError,
    has_compatible_wheel,
    interpreters_with_a_wheel,
)
from .wheelhouse import local_index, merged_index, wheel_for, wheelhouse_dir

ProgressFn = Callable[[str], None]

#: Where engine installs live. Deliberately not the repo's ``config/``: these are
#: tens of megabytes of third-party wheels, not the user's configuration.
DEFAULT_ENGINES_DIR = Path.home() / ".graphxr-proxy" / "engines"

#: A cold install downloads and unpacks 30-100MB and may also fetch an interpreter.
INSTALL_TIMEOUT_SECONDS = 900.0

#: How long a half-finished install may hold the lock before another process takes
#: it over. Longer than the install timeout, so a live install is never stolen.
LOCK_STALE_SECONDS = INSTALL_TIMEOUT_SECONDS + 120.0

MARKER_NAME = "runtime.json"

#: The post-install import check: a process start plus one engine import.
VERIFY_TIMEOUT_SECONDS = 90.0

#: The worker script, shared with ``pool`` so both start the engine the same way.
WORKER_SCRIPT = Path(__file__).with_name("worker.py")


class EngineInstallError(RuntimeError):
    """The engine could not be installed, with a reason worth showing a user."""


@dataclass(frozen=True)
class EngineRuntime:
    """One installed engine release, and how to start a process that can import it."""

    engine: str
    version: str
    root: Path
    #: The interpreter to run the worker with.
    python: Path
    #: Set for a ``--target`` install; the directory to put on ``PYTHONPATH``.
    site_dir: Optional[Path] = None
    #: The storage format this build writes, read from the engine during the
    #: post-install import check. Free here, and it is what teaches the version map.
    storage_version: Optional[int] = None

    def env(self) -> Dict[str, str]:
        """The environment for the worker process."""
        env = dict(os.environ)
        if self.site_dir is not None:
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                f"{self.site_dir}{os.pathsep}{existing}" if existing else str(self.site_dir)
            )
        # The engines are chatty about their own telemetry and, on Windows, about
        # console encoding. Both are noise in a pipe carrying JSON.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.setdefault("KUZU_DISABLE_TELEMETRY", "1")
        env.setdefault("LBUG_DISABLE_TELEMETRY", "1")
        return env

    def to_json(self) -> Dict[str, object]:
        return {
            "engine": self.engine,
            "version": self.version,
            "python": str(self.python),
            "site_dir": str(self.site_dir) if self.site_dir else None,
            "storage_version": self.storage_version,
            "installed_at": time.time(),
        }


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def engines_dir() -> Path:
    """The engines root; ``GRAPHXR_PROXY_ENGINES_DIR`` overrides it."""
    override = os.getenv("GRAPHXR_PROXY_ENGINES_DIR")
    return Path(override).expanduser() if override else DEFAULT_ENGINES_DIR


def runtime_root(engine: str, version: str) -> Path:
    return engines_dir() / f"{engine}-{version}"


def _venv_python(venv_root: Path) -> Path:
    return venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed_runtime(engine: str, version: str) -> Optional[EngineRuntime]:
    """
    The runtime for a release that is already on disk, or None.

    The marker is checked against the interpreter it names: a uv-managed Python that
    was cleaned up, or a home directory moved between machines, leaves a marker
    pointing at nothing, and reinstalling is better than failing at first query.
    """
    root = runtime_root(engine, version)
    marker = root / MARKER_NAME
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None

    python = Path(str(data.get("python") or ""))
    if not python.exists():
        return None
    site_dir = data.get("site_dir")
    site = Path(site_dir) if site_dir else None
    if site is not None and not site.is_dir():
        return None
    storage_version = data.get("storage_version")
    return EngineRuntime(
        engine=engine,
        version=version,
        root=root,
        python=python,
        site_dir=site,
        storage_version=storage_version if isinstance(storage_version, int) else None,
    )


# ---------------------------------------------------------------------------
# A lock that survives a crashed installer
# ---------------------------------------------------------------------------


class _InstallLock:
    """
    A cross-process lock for one install directory.

    An ``asyncio.Lock`` covers this proxy; two proxies sharing a home directory are
    unusual but not impossible, and a half-written engine install is the kind of
    corruption that is hard to diagnose later. The lock goes stale on its own so a
    killed installer cannot block the next one forever.
    """

    def __init__(self, path: Path):
        self.path = path
        self._held = False

    def __enter__(self) -> "_InstallLock":
        deadline = time.monotonic() + 30.0
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    # Someone else is installing this release. Their result is the
                    # one we would have produced, so wait for it at a higher level.
                    return self
                time.sleep(0.5)
            except OSError:
                # A read-only engines root: proceed unlocked rather than refuse.
                return self

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS
        except OSError:
            return True

    def __exit__(self, *_exc) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def uv_executable() -> Optional[str]:
    """``uv`` on PATH, or the one ``GRAPHXR_PROXY_UV`` names."""
    override = os.getenv("GRAPHXR_PROXY_UV")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("uv")


async def _run(command: List[str], on_progress: ProgressFn, env: Optional[Dict[str, str]] = None) -> None:
    """Run an installer command, forwarding its last line of output as progress."""
    on_progress(" ".join(Path(command[0]).name if index == 0 else part for index, part in enumerate(command)))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    tail: List[str] = []

    async def pump() -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            tail.append(line)
            del tail[:-20]
            on_progress(line)

    try:
        await asyncio.wait_for(
            asyncio.gather(pump(), process.wait()), timeout=INSTALL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        raise EngineInstallError(
            f"{Path(command[0]).name} timed out after {int(INSTALL_TIMEOUT_SECONDS)}s"
        ) from exc

    if process.returncode:
        detail = "\n".join(tail[-8:]) or "no output"
        raise EngineInstallError(
            f"{Path(command[0]).name} exited with {process.returncode}:\n{detail}"
        )


async def _index_for(engine: str) -> Optional[PackageIndex]:
    """
    What could be installed for this engine, or None when nothing can say.

    Wheels built on this machine are folded in, so a release the wheelhouse has and
    PyPI does not -- an engine with no wheel for this platform, which is the reason
    the wheelhouse exists -- is planned for like any other. A wheelhouse also
    answers when the index cannot: offline is not fatal, and a local file is still
    a local file.
    """
    local = local_index(engine)
    try:
        published = await PACKAGE_INDEX.get(engine)
    except PackageIndexError:
        return local if local.versions else None
    return merged_index(published, local) if local.versions else published


def _requirement(engine: str, version: str) -> str:
    """
    What to hand the installer for this release.

    A locally built wheel is named by path rather than by version, so "use the one I
    built" does not come down to a resolver's preference between two files claiming
    the same release.
    """
    wheel = wheel_for(engine, version)
    return str(wheel) if wheel is not None else f"{engine}=={version}"


def _wheel_platforms(index: PackageIndex, version: str) -> List[str]:
    """
    The platforms a release does publish wheels for, as their tag families.

    ``manylinux_2_17_x86_64`` and ``manylinux_2_17_aarch64`` are one answer to the
    question being asked -- "where would this run?" -- so they collapse to
    ``manylinux``. Sorted, because a set in a message reads differently every time.
    """
    families = set()
    for tag in index.tags_for(version):
        platform = tag.rsplit("-", 1)[-1]
        if platform == "any":
            continue
        for family in ("manylinux", "musllinux", "macosx", "win"):
            if platform.startswith(family):
                families.add(family)
                break
        else:
            families.add(platform)
    return sorted(families)


def _describe_wheel_gap(engine: str, version: str, index: PackageIndex) -> str:
    """The message for a release with no wheel this machine can use."""
    running = f"CPython 3.{sys.version_info.minor}"
    usable = interpreters_with_a_wheel(index, version)
    if usable:
        wheels = ", ".join(f"3.{minor}" for minor in usable)
        return (
            f"{engine} {version} publishes no wheel for {running} on this platform. "
            f"It has wheels for Python {wheels}; install uv so the proxy can run the "
            f"engine on one of them, or run the proxy on one of those interpreters."
        )
    # No interpreter here can use it, so the gap is the platform rather than the
    # Python version -- which is a different thing to tell the user, and the only
    # thing that would help them. LatticeDB is the case in point: it publishes
    # macOS and manylinux wheels and no Windows wheel at all, so on Windows the
    # answer is a Linux host or the Docker image, never another interpreter.
    platforms = _wheel_platforms(index, version)
    where = f" It publishes wheels for {', '.join(platforms)}." if platforms else ""
    return (
        f"{engine} {version} publishes no wheel for this platform at all "
        f"(running {running}).{where} Only a source distribution is available, and "
        f"building it needs the engine's own native toolchain. Build a wheel "
        f"yourself and drop it in {wheelhouse_dir()}; the proxy installs from there "
        f"in preference to the index."
    )


async def install_runtime(
    engine: str,
    version: str,
    on_progress: Optional[ProgressFn] = None,
) -> EngineRuntime:
    """
    Put ``engine==version`` on disk and return how to run it.

    Returns the existing install straight away when there is one, so this is safe to
    call on every connection.
    """
    progress: ProgressFn = on_progress or (lambda _line: None)

    existing = installed_runtime(engine, version)
    if existing is not None:
        return existing

    root = runtime_root(engine, version)
    index = await _index_for(engine)

    with _InstallLock(root.parent / f".{engine}-{version}.lock"):
        # Another process may have finished while we waited for the lock.
        existing = installed_runtime(engine, version)
        if existing is not None:
            return existing

        uv = uv_executable()
        if uv:
            runtime = await _install_with_uv(uv, engine, version, root, index, progress)
        else:
            runtime = await _install_with_pip(engine, version, root, index, progress)

        marker = root / MARKER_NAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(runtime.to_json(), handle, indent=2)
        progress(f"{engine} {version} ready")
        return runtime


@dataclass(frozen=True)
class _InterpreterChoice:
    """One interpreter to try, and how to ask uv for it."""

    #: What ``uv venv --python`` is given: a full path, or a bare "3.13".
    spec: str
    label: str
    #: Force a uv-downloaded CPython rather than one found on PATH.
    managed_only: bool = False


def _interpreter_plan(
    engine: str, version: str, index: Optional[PackageIndex]
) -> List[_InterpreterChoice]:
    """
    Interpreters to try for this release, best first.

    Having a wheel is necessary but not sufficient, which is why this is a list and
    not a choice. A wheel can install cleanly and still fail to import: the Ladybug
    Windows wheel needs ``libssl-3-x64.dll``, and a CPython that ships OpenSSL under
    the unsuffixed ``libssl-3.dll`` -- as some Windows builds do -- cannot load it no
    matter how right the tags were. A uv-managed CPython is the reliable fallback,
    because it always carries the standard names.
    """
    running = f"the proxy's own CPython 3.{sys.version_info.minor}"
    if index is None:
        # Offline: the only interpreter we can reason about is this one.
        return [_InterpreterChoice(spec=sys.executable, label=running)]

    plan: List[_InterpreterChoice] = []
    if has_compatible_wheel(index, version):
        plan.append(_InterpreterChoice(spec=sys.executable, label=running))

    for minor in interpreters_with_a_wheel(index, version):
        plan.append(
            _InterpreterChoice(
                spec=f"3.{minor}",
                label=f"a uv-managed CPython 3.{minor}",
                managed_only=True,
            )
        )
    return plan


async def _install_with_uv(
    uv: str,
    engine: str,
    version: str,
    root: Path,
    index: Optional[PackageIndex],
    progress: ProgressFn,
) -> EngineRuntime:
    """A private virtual environment, on an interpreter that can actually load the engine."""
    plan = _interpreter_plan(engine, version, index)
    if not plan:
        raise EngineInstallError(_describe_wheel_gap(engine, version, index))

    requirement = _requirement(engine, version)
    if requirement != f"{engine}=={version}":
        progress(f"installing {Path(requirement).name} from the wheelhouse")

    venv_root = root / "venv"
    failures: List[str] = []

    for choice in plan:
        if failures:
            progress(f"retrying {engine} {version} on {choice.label}")
        # A previous attempt's environment would otherwise be reused by uv.
        shutil.rmtree(venv_root, ignore_errors=True)

        # Always name the interpreter. Left to itself, ``uv venv`` builds against
        # whatever CPython it discovers on PATH -- which on this box is the 3.13
        # that ships uv, not the 3.14 running the proxy -- so the venv would have
        # nothing to do with the wheel check just performed for this interpreter.
        command = [uv, "venv", str(venv_root), "--python", choice.spec]
        if choice.managed_only:
            command += ["--python-preference", "only-managed"]

        try:
            await _run(command, progress)
            python = _venv_python(venv_root)
            await _run(
                [uv, "pip", "install", "--python", str(python), requirement],
                progress,
            )
        except EngineInstallError as exc:
            failures.append(f"{choice.label}: {exc}")
            continue

        runtime = EngineRuntime(engine=engine, version=version, root=root, python=python)
        verified, problem = await verify_runtime(runtime)
        if verified is not None:
            return verified
        progress(f"{engine} {version} installed but would not run on {choice.label}")
        failures.append(f"{choice.label}: {problem}")

    raise EngineInstallError(
        f"Could not get a working {engine} {version} on this machine.\n"
        + "\n".join(failures)
    )


async def _install_with_pip(
    engine: str,
    version: str,
    root: Path,
    index: Optional[PackageIndex],
    progress: ProgressFn,
) -> EngineRuntime:
    """
    A ``--target`` install against the running interpreter.

    Without uv there is no way to obtain a different interpreter, so a release with
    no wheel for this one is refused here with the list of interpreters that would
    work -- rather than handed to pip, which would try to build it from source.
    """
    if index is not None and not has_compatible_wheel(index, version):
        raise EngineInstallError(_describe_wheel_gap(engine, version, index))

    requirement = _requirement(engine, version)
    if requirement != f"{engine}=={version}":
        progress(f"installing {Path(requirement).name} from the wheelhouse")

    site = root / "site"
    site.mkdir(parents=True, exist_ok=True)
    await _run(
        [
            sys.executable, "-m", "pip", "install",
            "--only-binary", ":all:",
            "--target", str(site),
            requirement,
        ],
        progress,
    )
    runtime = EngineRuntime(
        engine=engine, version=version, root=root, python=Path(sys.executable), site_dir=site
    )
    verified, problem = await verify_runtime(runtime)
    if verified is None:
        raise EngineInstallError(
            f"{engine} {version} installed for this interpreter but would not run: {problem}. "
            f"Install uv so the proxy can run the engine on an interpreter of its own."
        )
    return verified


async def verify_runtime(runtime: EngineRuntime) -> Tuple[Optional[EngineRuntime], str]:
    """
    Start the worker once and ask the engine what it is.

    An install is not finished when the files are on disk -- it is finished when the
    extension loads. Rehearsing that here, through the same script the driver will
    use, is what turns "the wheel matched the tags" into "this actually works", and
    it costs one short process. The storage version comes back with it, so the
    version map learns what this build writes without a second launch.

    Returns ``(runtime, "")`` on success and ``(None, reason)`` on failure; callers
    decide whether a failure is fatal or worth another interpreter.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            str(runtime.python),
            "-u",
            str(WORKER_SCRIPT),
            runtime.engine,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=runtime.env(),
        )
    except OSError as exc:
        return None, str(exc)

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(b'{"op":"info","id":1}\n{"op":"shutdown","id":2}\n'),
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        return None, f"the engine did not answer within {int(VERIFY_TIMEOUT_SECONDS)}s"

    for raw in stdout.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or "ok" not in message:
            continue
        if not message.get("ok"):
            return None, str(message.get("error") or "the engine reported an error")
        storage_version = message.get("storage_version")
        return (
            EngineRuntime(
                engine=runtime.engine,
                version=runtime.version,
                root=runtime.root,
                python=runtime.python,
                site_dir=runtime.site_dir,
                storage_version=storage_version if isinstance(storage_version, int) else None,
            ),
            "",
        )

    tail = [line for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    return None, (tail[-1] if tail else "the engine produced no answer")


def remove_runtime(engine: str, version: str) -> bool:
    """Delete an install. Used when a probe proves the release cannot open the store."""
    root = runtime_root(engine, version)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return not root.exists()
