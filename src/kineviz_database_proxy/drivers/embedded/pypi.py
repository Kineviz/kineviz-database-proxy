# -*- coding: utf-8 -*-
"""
What releases exist, and which of them have a wheel this machine can install.

The second question is not academic. This box runs CPython 3.14, and ``kuzu``
0.11.3 publishes ``cp314`` wheels for Linux only -- so a plain
``pip install kuzu==0.11.3`` here falls through to a source build and fails after
several minutes of CMake. Checking the file list first turns that into a decision:
either run the engine on an interpreter that does have a wheel, or say which
interpreters would work.

One request answers both questions. PyPI's JSON simple index returns every
filename and every version for a package in a single response, which is smaller
than the legacy ``/pypi/{name}/json`` document and does not grow with the number of
releases the way per-release lookups do.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

SIMPLE_INDEX_URL = "https://pypi.org/simple/{package}/"
SIMPLE_INDEX_ACCEPT = "application/vnd.pypi.simple.v1+json"

#: The index changes when a release is published, which is not often. Long enough
#: that a burst of lookups costs one request, short enough that a release published
#: this morning is visible this afternoon.
INDEX_TTL_SECONDS = 900.0

REQUEST_TIMEOUT_SECONDS = 15.0

#: ``kuzu-0.11.3-cp313-cp313-win_amd64.whl`` -> version and the three tag fields.
_WHEEL_RE = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)"
    r"-(?:(?P<build>\d[^-]*)-)?"
    r"(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$",
    re.IGNORECASE,
)

#: Interpreter minors worth considering when the running one has no wheel. Ordered
#: newest first: a fallback interpreter should still be a current one.
FALLBACK_PYTHON_MINORS: Tuple[int, ...] = (13, 12, 11, 10, 9)


class PackageIndexError(RuntimeError):
    """The index could not be read. Callers fall back to the seeded map."""


@dataclass
class PackageIndex:
    """One package's releases and the wheel tags each of them published."""

    package: str
    versions: List[str] = field(default_factory=list)
    #: version -> the set of ``py-abi-platform`` tag triples published for it.
    wheel_tags: Dict[str, Set[str]] = field(default_factory=dict)
    #: version -> whether an sdist exists, which is the only thing left to try when
    #: no wheel matches. Recorded but never preferred: these engines are a C++ build.
    has_sdist: Dict[str, bool] = field(default_factory=dict)

    def tags_for(self, version: str) -> Set[str]:
        return self.wheel_tags.get(version, set())


# ---------------------------------------------------------------------------
# Tag matching
# ---------------------------------------------------------------------------


def _expand(python_field: str, abi_field: str, platform_field: str) -> Set[str]:
    """A wheel's compressed tag set, e.g. ``cp39.cp310-abi3-win_amd64``, expanded."""
    return {
        f"{py}-{abi}-{plat}"
        for py in python_field.split(".")
        for abi in abi_field.split(".")
        for plat in platform_field.split(".")
    }


def parse_wheel_filename(filename: str) -> Optional[Tuple[str, str, Set[str]]]:
    """
    ``(package, version, tags)`` for a wheel filename, or None if it is not one.

    Shared with the wheelhouse, which asks the same question of a file on disk that
    this module asks of a name in an index -- and a second copy of PEP 427 is a
    second answer waiting to disagree with this one.
    """
    match = _WHEEL_RE.match(filename)
    if match is None:
        return None
    return (
        match.group("name"),
        match.group("version"),
        _expand(match.group("python"), match.group("abi"), match.group("platform")),
    )


def supported_tags(python_minor: Optional[int] = None) -> Set[str]:
    """
    The wheel tags an interpreter on *this* platform would accept.

    ``python_minor`` asks the question about a different CPython than the running
    one, on the same machine -- which is exactly what is needed to answer "would
    3.12 have worked?" without installing 3.12 to find out.

    Falls back to a coarse match if ``packaging`` is somehow absent; that only costs
    precision on the exotic platform tags, and the alternative is refusing to
    install anything.
    """
    try:
        from packaging import tags as packaging_tags
    except ImportError:  # pragma: no cover - packaging is a declared dependency
        return _coarse_tags(python_minor)

    platforms = list(packaging_tags.platform_tags())
    if python_minor is None or python_minor == sys.version_info.minor:
        return {str(tag) for tag in packaging_tags.sys_tags()}

    version = (sys.version_info.major, python_minor)
    collected = {
        str(tag)
        for tag in packaging_tags.cpython_tags(python_version=version, platforms=platforms)
    }
    collected.update(
        str(tag)
        for tag in packaging_tags.compatible_tags(python_version=version, platforms=platforms)
    )
    return collected


def _coarse_tags(python_minor: Optional[int]) -> Set[str]:
    """A best-effort tag set for a machine without ``packaging``."""
    minor = sys.version_info.minor if python_minor is None else python_minor
    if sys.platform == "win32":
        platforms = ["win_amd64", "win32", "any"]
    elif sys.platform == "darwin":
        platforms = ["macosx_11_0_arm64", "macosx_11_0_x86_64", "any"]
    else:
        platforms = ["manylinux_2_28_x86_64", "manylinux2014_x86_64", "linux_x86_64", "any"]
    pythons = [f"cp3{minor}", f"py3{minor}", "py3"]
    abis = [f"cp3{minor}", "abi3", "none"]
    return {f"{py}-{abi}-{plat}" for py in pythons for abi in abis for plat in platforms}


def has_compatible_wheel(
    index: PackageIndex, version: str, python_minor: Optional[int] = None
) -> bool:
    """Whether that release ships a wheel an interpreter on this machine can use."""
    return bool(index.tags_for(version) & supported_tags(python_minor))


def interpreters_with_a_wheel(
    index: PackageIndex, version: str, minors: Sequence[int] = FALLBACK_PYTHON_MINORS
) -> List[int]:
    """
    Which CPython minors could install this release here, newest first.

    Used both to pick a fallback interpreter and to write an error message that
    tells the user something actionable instead of showing them a failed C++ build.
    """
    return [minor for minor in minors if has_compatible_wheel(index, version, minor)]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def parse_simple_index(package: str, payload: dict) -> PackageIndex:
    """The JSON simple index, reduced to the two things this module is asked about."""
    index = PackageIndex(package=package)
    versions = payload.get("versions")
    if isinstance(versions, list):
        index.versions = [str(v) for v in versions]

    seen: Set[str] = set(index.versions)
    for entry in payload.get("files") or []:
        filename = str((entry or {}).get("filename") or "")
        if (entry or {}).get("yanked"):
            # A yanked release is one the publisher withdrew; installing it on a
            # user's behalf would be picking up something they pulled on purpose.
            continue
        if filename.endswith(".whl"):
            parsed = parse_wheel_filename(filename)
            if parsed is None:
                continue
            _, version, tags = parsed
            index.wheel_tags.setdefault(version, set()).update(tags)
        elif filename.endswith((".tar.gz", ".zip")):
            stem = filename[: -len(".tar.gz")] if filename.endswith(".tar.gz") else filename[:-4]
            prefix = f"{package}-"
            if not stem.lower().startswith(prefix.lower()):
                continue
            version = stem[len(prefix) :]
            index.has_sdist[version] = True
        else:
            continue
        if version not in seen:
            seen.add(version)
            index.versions.append(version)

    return index


class PackageIndexCache:
    """One index per package, refreshed on a TTL, single-flighted across callers."""

    def __init__(self, ttl: float = INDEX_TTL_SECONDS):
        self.ttl = ttl
        self._entries: Dict[str, Tuple[float, PackageIndex]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def peek(self, package: str) -> Optional[PackageIndex]:
        entry = self._entries.get(package)
        if entry is None:
            return None
        stored_at, index = entry
        if time.monotonic() - stored_at > self.ttl:
            return None
        return index

    async def get(self, package: str, refresh: bool = False) -> PackageIndex:
        if not refresh:
            cached = self.peek(package)
            if cached is not None:
                return cached

        lock = self._locks.setdefault(package, asyncio.Lock())
        async with lock:
            if not refresh:
                cached = self.peek(package)
                if cached is not None:
                    return cached
            index = await self._fetch(package)
            self._entries[package] = (time.monotonic(), index)
            return index

    async def _fetch(self, package: str) -> PackageIndex:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a declared dependency
            raise PackageIndexError("httpx is not installed") from exc

        url = SIMPLE_INDEX_URL.format(package=package)
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers={"Accept": SIMPLE_INDEX_ACCEPT})
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise PackageIndexError(f"Could not read {url}: {exc}") from exc

        return parse_simple_index(package, payload)


#: Process-wide, because drivers are built per request.
PACKAGE_INDEX = PackageIndexCache()


async def list_releases(package: str) -> List[str]:
    """Every non-yanked version of a package, unordered. Raises on a failed fetch."""
    return list((await PACKAGE_INDEX.get(package)).versions)
