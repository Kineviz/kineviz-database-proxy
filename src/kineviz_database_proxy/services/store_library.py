# -*- coding: utf-8 -*-
"""
The store library: the files the proxy keeps on its own disk.

An embedded project points at a path, and that path is usually a file somebody
dropped onto the proxy. Those land under ``config/databases`` and, until now,
nothing could see them again -- not the form that wanted to reuse one, and not the
person who wanted to know what was taking up the disk. This module is that view.

Two things it deliberately does *not* do:

**It does not probe everything it walks.** Reading a header is cheap for one file
and not cheap for ten thousand, so the walk yields names, and only the page being
returned is stat-ed and read. That is what makes ``offset``/``limit`` real rather
than a slice of an already-loaded list.

**It does not decide what is servable on its own.** A file whose first bytes are
not an embedded-store magic number is still listed -- with whatever it *is*, when
that can be named. A library that hides what it does not understand is a library
that makes files disappear.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..drivers.embedded.store_probe import (
    FAMILY_NAMES,
    HEADER_SIZE,
    LAYOUT_DIRECTORY,
    LAYOUT_FILE,
    header_file_for,
    parse_header,
)

#: Where dropped stores land. Local state, next to the rest of it, and gitignored.
DEFAULT_STORES_DIR = Path("config") / "databases"

#: Enough for every magic number below. SQLite's is the longest at 16 bytes;
#: DuckDB's sits at offset 8, and the embedded engines' at 0.
IDENTIFY_BYTES = 16

#: Kinds the library can name but the proxy cannot serve yet. Listing a SQLite
#: file as "SQLite database" rather than "not a store" is the difference between
#: a file manager and a filter: the file is really there either way, and the user
#: is the one who has to decide what to do about it.
#:
#: ``(kind, magic, offset, label)``
OTHER_FORMATS: Tuple[Tuple[str, bytes, int, str], ...] = (
    ("sqlite", b"SQLite format 3\x00", 0, "SQLite database"),
    ("duckdb", b"DUCK", 8, "DuckDB database"),
)

KIND_UNKNOWN = "unknown"

#: Names the walk never reports: the upload route's in-progress temporary files,
#: and the usual filesystem debris.
HIDDEN_PREFIXES = (".",)


class StoreLibraryError(RuntimeError):
    """A request that the library refuses, with a reason meant for a user."""


def stores_dir() -> Path:
    """Where uploads are kept; ``GRAPHXR_PROXY_STORES_DIR`` overrides it."""
    override = os.getenv("GRAPHXR_PROXY_STORES_DIR")
    return Path(override).expanduser() if override else DEFAULT_STORES_DIR


@dataclass(frozen=True)
class LibraryEntry:
    """One thing in the library, identified as far as it can be."""

    #: Path relative to the library root, with forward slashes. This is the id the
    #: API takes back for delete, and it is stable across platforms.
    relative_path: str
    path: str
    folder: str
    name: str
    size: int
    modified: float
    layout: str
    #: One of ``ENGINES``, one of ``OTHER_FORMATS``, or "unknown".
    kind: str
    #: The engine that can serve it -- set only for a store this proxy can open.
    engine: Optional[str]
    storage_version: Optional[int]
    description: str

    @property
    def servable(self) -> bool:
        return self.engine is not None


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


def _visible(name: str) -> bool:
    return not name.startswith(HIDDEN_PREFIXES)


def _is_directory_store(path: Path) -> bool:
    """A directory that is itself a store, rather than a folder holding some."""
    if not path.is_dir():
        return False
    return header_file_for(path) is not None


def iter_paths(root: Path, search: str = "") -> Iterator[Path]:
    """
    Every store-shaped path under ``root``, in a stable order, lazily.

    A generator rather than a list because the caller pages over it: a listing of
    the tenth page must not pay for stat-ing the first nine. Names are sorted per
    directory so the same page is the same page twice running -- ``scandir`` order
    is not guaranteed and differs between filesystems.

    A directory that carries a catalog is a leaf: it is one store written by Kuzu
    0.10 or older, not a folder holding several.
    """
    if not root.is_dir():
        return
    needle = search.strip().lower()

    def walk(directory: Path) -> Iterator[Path]:
        try:
            names = sorted(entry.name for entry in os.scandir(directory) if _visible(entry.name))
        except OSError:
            return
        directories: List[Path] = []
        for name in names:
            child = directory / name
            if child.is_dir():
                if _is_directory_store(child):
                    if not needle or needle in name.lower():
                        yield child
                else:
                    directories.append(child)
            elif child.is_file():
                if not needle or needle in name.lower():
                    yield child
        for child in directories:
            yield from walk(child)

    yield from walk(root)


def count_paths(root: Path, search: str = "") -> int:
    """How many entries the walk would yield. Names only -- no stat, no read."""
    return sum(1 for _ in iter_paths(root, search))


def folders(root: Path) -> List[str]:
    """The folders an upload can be filed under: direct children that hold stores."""
    if not root.is_dir():
        return []
    out = []
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and _visible(entry.name) and not _is_directory_store(Path(entry.path)):
                out.append(entry.name)
    except OSError:
        return []
    return sorted(out)


# ---------------------------------------------------------------------------
# Identifying
# ---------------------------------------------------------------------------


def identify(prefix: bytes) -> Tuple[str, Optional[str], Optional[int], str]:
    """
    ``(kind, engine, storage_version, description)`` for a file's first bytes.

    The embedded engines are asked first and answer with a format number; the rest
    are recognised by magic alone, because nothing here can open them anyway.
    """
    parsed = parse_header(prefix[:HEADER_SIZE])
    if parsed is not None:
        engine, storage_version = parsed
        family = FAMILY_NAMES.get(engine, engine)
        return engine, engine, storage_version, f"{family} store, storage version {storage_version}"

    for kind, magic, offset, label in OTHER_FORMATS:
        if prefix[offset : offset + len(magic)] == magic:
            return kind, None, None, label

    return KIND_UNKNOWN, None, None, "Unrecognised file"


def _read_prefix(path: Path) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(IDENTIFY_BYTES)
    except OSError:
        return b""


def describe(root: Path, path: Path) -> LibraryEntry:
    """Stat and read one path. The expensive half, done only for a page."""
    header_path = header_file_for(path) or path
    prefix = _read_prefix(header_path) if header_path.is_file() else b""
    kind, engine, storage_version, description = identify(prefix)

    try:
        stat = path.stat()
        size = _directory_size(path) if path.is_dir() else stat.st_size
        modified = stat.st_mtime
    except OSError:
        size = 0
        modified = 0.0

    relative = path.relative_to(root)
    parent = relative.parent.as_posix()
    return LibraryEntry(
        relative_path=relative.as_posix(),
        path=str(path),
        folder="" if parent == "." else parent,
        name=path.name,
        size=size,
        modified=modified,
        layout=LAYOUT_DIRECTORY if path.is_dir() else LAYOUT_FILE,
        kind=kind,
        engine=engine,
        storage_version=storage_version,
        description=description,
    )


def _directory_size(path: Path) -> int:
    """A directory-layout store's size is the sum of its parts."""
    total = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


def page(root: Path, offset: int, limit: int, search: str = "") -> List[LibraryEntry]:
    """
    One page of the library, identified.

    The walk is consumed lazily and abandoned once the page is full, so the cost is
    ``offset`` cheap steps plus ``limit`` expensive ones -- not the whole tree.
    """
    out: List[LibraryEntry] = []
    for index, path in enumerate(iter_paths(root, search)):
        if index < offset:
            continue
        out.append(describe(root, path))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Resolving a request back to a path
# ---------------------------------------------------------------------------


def resolve(root: Path, relative_path: str) -> Path:
    """
    A library-relative path, checked back to a real one inside the root.

    The check is on the *resolved* path rather than on the text: a caller can spell
    an escape in more ways than ``..`` -- a symlink is enough -- and only the
    filesystem knows where one really lands.
    """
    text = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if not text:
        raise StoreLibraryError("No path was given")

    base = root.resolve()
    target = (root / text).resolve()
    if target != base and base not in target.parents:
        raise StoreLibraryError(f"{relative_path} is outside the store library")
    if target == base:
        raise StoreLibraryError("The library root itself cannot be used here")
    return target


def remove(root: Path, relative_path: str) -> str:
    """
    Delete one entry, and the folder it leaves empty behind it.

    A directory is only removed when it is itself a store -- a folder full of other
    people's stores is not something a delete button should be able to take out by
    naming its parent.
    """
    target = resolve(root, relative_path)
    if not target.exists():
        raise StoreLibraryError(f"{relative_path} is not in the store library")

    if target.is_dir():
        if not _is_directory_store(target):
            raise StoreLibraryError(
                f"{relative_path} is a folder rather than a store. Delete the stores "
                f"inside it and the folder goes with the last one."
            )
        shutil.rmtree(target)
    else:
        target.unlink()

    _prune_empty_parents(root.resolve(), target.parent)
    return str(target)


def _prune_empty_parents(base: Path, directory: Path) -> None:
    """Walk back up removing directories the delete emptied, stopping at the root."""
    current = directory
    while current != base and base in current.parents:
        try:
            if any(os.scandir(current)):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


# ---------------------------------------------------------------------------
# Who is using what
# ---------------------------------------------------------------------------


def _normalise(path: str) -> str:
    """A path in the one spelling two of them can be compared in."""
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except OSError:
        return os.path.normcase(str(path))


def usage_index(projects: Iterable) -> Dict[str, List[str]]:
    """
    ``{normalised path: [project name, ...]}`` for every project that names one.

    Built once per request and looked up per row: the alternative is re-reading
    ``projects.json`` for each file on the page.
    """
    index: Dict[str, List[str]] = {}
    for project in projects:
        config = getattr(project, "database_config", None)
        raw = getattr(config, "database_path", None) if config else None
        if not raw:
            continue
        index.setdefault(_normalise(raw), []).append(getattr(project, "name", ""))
    return index


def used_by(index: Dict[str, List[str]], path: str) -> List[str]:
    return index.get(_normalise(path), [])


def external_paths(
    projects: Iterable, root: Path
) -> List[Tuple[str, str, Sequence[str]]]:
    """
    ``(path, database_type, [project name, ...])`` for stores kept outside the library.

    A project can point anywhere on the disk, and one that does is still worth
    offering back in the path field -- it is a store the user has already told the
    proxy about. Bounded by the number of embedded projects, so it is not paged.
    """
    base = os.path.normcase(str(root.resolve())) if root.exists() else os.path.normcase(str(root))
    grouped: Dict[str, Tuple[str, List[str]]] = {}
    for project in projects:
        config = getattr(project, "database_config", None)
        raw = getattr(config, "database_path", None) if config else None
        if not raw:
            continue
        resolved = _normalise(raw)
        if resolved == base or resolved.startswith(base + os.sep):
            continue
        raw_type = getattr(project, "database_type", "") or ""
        # A str-mixin Enum's str() is "DatabaseType.KUZU"; the value is the route.
        database_type = str(getattr(raw_type, "value", raw_type))
        entry = grouped.setdefault(str(raw), (database_type, []))
        entry[1].append(getattr(project, "name", ""))
    return [(path, kind, names) for path, (kind, names) in sorted(grouped.items())]
