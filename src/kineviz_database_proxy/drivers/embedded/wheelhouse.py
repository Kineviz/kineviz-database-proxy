# -*- coding: utf-8 -*-
"""
Wheels built on this machine, preferred over anything the index publishes.

Not every engine has a wheel for every platform, and the gap is not always an
oversight. LatticeDB is a Zig project whose CI has no Windows job, so on Windows
there is nothing to install -- even though the engine cross-compiles for it in one
command and runs fine once it is built. Until now a user who built their own was
left holding a file the proxy had no way to accept.

This is that way. A wheel dropped into the wheelhouse is offered to the installer
as if it were a published release: same version, same tag matching, same
post-install import check. Nothing here trusts a filename beyond what PEP 427
already guarantees, and the tags still decide whether a wheel is usable here --
by the same code that decides it for a published one, not a second copy of it.

Two properties worth stating, because both are choices:

  - **Local wins.** When the wheelhouse and the index both offer a version, the
    local file is installed *by path*, so "use the one I built" is not left to a
    resolver's preference between two files claiming the same release.
  - **Only the engine comes from here.** The wheel's own dependencies still resolve
    normally, which is why this is a path rather than ``--no-index``: LatticeDB
    needs numpy, and a wheelhouse holding one file should not have to hold numpy's
    build matrix as well.

Air-gapped installs get the same mechanism for free.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator, Optional, Tuple

from .pypi import PackageIndex, parse_wheel_filename, supported_tags

#: Beside the engine installs rather than inside one: a wheel here outlives any
#: single install and is what a reinstall should reach for again.
DEFAULT_WHEELHOUSE_DIR = Path.home() / ".graphxr-proxy" / "wheelhouse"

_NORMALISE = re.compile(r"[-_.]+")


def wheelhouse_dir() -> Path:
    """Where locally built wheels live; ``GRAPHXR_PROXY_WHEELHOUSE`` overrides it."""
    override = os.getenv("GRAPHXR_PROXY_WHEELHOUSE")
    return Path(override).expanduser() if override else DEFAULT_WHEELHOUSE_DIR


def _normalise(name: str) -> str:
    """PEP 503 name comparison: ``Lattice_DB`` and ``lattice-db`` are one package."""
    return _NORMALISE.sub("-", name).lower()


def local_wheels(package: str) -> Iterator[Tuple[str, Path]]:
    """
    ``(version, path)`` for every wheel in the wheelhouse belonging to ``package``.

    A generator over one flat directory, sorted by name so the answer is the same
    twice running. Nothing is opened: a wheel's version and tags are in its
    filename, which is the whole reason this can be a directory listing rather than
    an index.
    """
    root = wheelhouse_dir()
    wanted = _normalise(package)
    try:
        names = sorted(entry.name for entry in os.scandir(root) if entry.is_file())
    except OSError:
        # No wheelhouse is the normal case, not an error worth reporting.
        return
    for name in names:
        if not name.endswith(".whl"):
            continue
        parsed = parse_wheel_filename(name)
        if parsed is None:
            continue
        wheel_package, version, _tags = parsed
        if _normalise(wheel_package) == wanted:
            yield version, root / name


def local_index(package: str) -> PackageIndex:
    """The wheelhouse as a package index, so the installer can plan against it."""
    index = PackageIndex(package=package)
    for version, path in local_wheels(package):
        parsed = parse_wheel_filename(path.name)
        if parsed is None:  # pragma: no cover - local_wheels already parsed it
            continue
        index.wheel_tags.setdefault(version, set()).update(parsed[2])
        if version not in index.versions:
            index.versions.append(version)
    return index


def merged_index(published: PackageIndex, local: PackageIndex) -> PackageIndex:
    """
    One index describing both sources, without mutating either.

    The published index is a shared cache entry, so folding local tags into it in
    place would leak this machine's build into every later lookup.
    """
    merged = PackageIndex(
        package=published.package,
        versions=list(published.versions),
        wheel_tags={version: set(tags) for version, tags in published.wheel_tags.items()},
        has_sdist=dict(published.has_sdist),
    )
    for version, tags in local.wheel_tags.items():
        merged.wheel_tags.setdefault(version, set()).update(tags)
        if version not in merged.versions:
            merged.versions.append(version)
    return merged


def wheel_for(package: str, version: str) -> Optional[Path]:
    """
    The locally built wheel to install for this release, or None.

    Only a wheel this machine could actually load is offered. A wheelhouse shared
    between a laptop and a build server holds files for both, and handing pip a
    wheel tagged for the other one turns a clear "no wheel for this platform" into
    an install that succeeds and then cannot import.
    """
    usable = supported_tags()
    for candidate_version, path in local_wheels(package):
        if candidate_version != version:
            continue
        parsed = parse_wheel_filename(path.name)
        if parsed is not None and parsed[2] & usable:
            return path
    return None
