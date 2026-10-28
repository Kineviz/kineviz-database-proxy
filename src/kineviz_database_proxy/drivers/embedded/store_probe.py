# -*- coding: utf-8 -*-
"""
What is this file? Read the header and find out.

Every embedded store starts with four magic bytes naming the family, and then the
storage version -- but *not* in the same field, which is why the width is looked up
per family rather than assumed::

    4b 55 5a 55  27 00 00 00 00 00 00 00     KUZU, storage version 39  (Kuzu 0.11.x)
    4c 42 55 47  2b 00 00 00 00 00 00 00     LBUG, storage version 43  (Ladybug 0.19.x)
    42 44 54 4c  03 00 03 00 00 10 00 00     BDTL, storage version 3   (LatticeDB 0.9+)

Kuzu and Ladybug write a little-endian ``uint64``. Reading the whole 64-bit field
rather than the single byte after the magic is deliberate: today they are the same
answer -- every released version fits in one byte -- and they stop being the same
answer at 256.

LatticeDB is a separate project rather than a fork, and its header says so. The
format number is a ``uint16``, and it is written *twice*: the second copy is the
oldest reader that may open the file, which is why both fields move together. Read
as a ``uint64`` the way the other two are, those trailing bytes -- the 4096-byte
page size among them -- would turn format 3 into 17,592,186,109,955.

Two shapes have to be recognised, because the layout changed mid-life:

  - **A single file.** Kuzu from 0.11, and every Ladybug and LatticeDB release. The
    header is at the start of the file itself.
  - **A directory.** Kuzu 0.10 and older wrote ``catalog.kz``, ``data.kz``,
    ``metadata.kz`` and the hash indexes side by side, and it is ``catalog.kz``
    that carries the magic; ``data.kz`` is empty in a fresh store.

Probing before opening is not politeness. Handing a store to the wrong engine
fails, but it fails badly: Ladybug on a Kuzu file at least says "not a valid Lbug
database file", while Kuzu 0.10 on a 0.11 file raises ``UnicodeDecodeError`` from
inside its catalog reader. Neither is something to show a user.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

#: The four bytes each family stamps at offset 0.
KUZU_MAGIC = b"KUZU"
LADYBUG_MAGIC = b"LBUG"
#: LatticeDB writes its name little-endian, so "LTDB" lands on disk as "BDTL".
LATTICE_MAGIC = b"BDTL"

MAGIC_TO_ENGINE = {
    KUZU_MAGIC: "kuzu",
    LADYBUG_MAGIC: "ladybug",
    LATTICE_MAGIC: "latticedb",
}

#: Engine names as they appear in ``DatabaseType`` and in the PyPI package name.
KUZU = "kuzu"
LADYBUG = "ladybug"
LATTICEDB = "latticedb"
ENGINES = (KUZU, LADYBUG, LATTICEDB)

#: Where each family keeps its format number: ``(offset, width)`` in bytes. The
#: width is the whole reason this table exists -- see the module docstring.
VERSION_FIELD = {
    KUZU: (4, 8),
    LADYBUG: (4, 8),
    LATTICEDB: (4, 2),
}

#: 4 magic bytes plus the widest storage-version field. Enough for every family, so
#: one prefix length still serves the upload check that reads before writing.
HEADER_SIZE = 12

#: The file inside a directory-layout store that carries the header. ``catalog.kz``
#: is what Kuzu 0.6 through 0.10 wrote; ``catalog.bin`` is the older spelling.
CATALOG_FILENAMES = ("catalog.kz", "catalog.bin")

LAYOUT_FILE = "file"
LAYOUT_DIRECTORY = "directory"

#: How each family is spelled when a message is shown to a person.
FAMILY_NAMES = {KUZU: "Kuzu", LADYBUG: "Ladybug", LATTICEDB: "LatticeDB"}


class StoreProbeError(ValueError):
    """A path that is not an embedded store, or one that cannot be read."""


@dataclass(frozen=True)
class StoreFingerprint:
    """What the bytes say about a store."""

    #: One of ``ENGINES``.
    engine: str
    #: The on-disk format number, e.g. 39 for Kuzu 0.11.x, 43 for Ladybug 0.19.x,
    #: 3 for LatticeDB 0.9 and up.
    storage_version: int
    #: The path to hand the engine -- the directory for an old store, the file itself
    #: for a new one. Not necessarily where the header was found.
    path: Path
    #: The file the header was actually read from, for error messages.
    header_path: Path
    layout: str

    @property
    def is_kuzu(self) -> bool:
        return self.engine == KUZU

    def describe(self) -> str:
        """One line for a form or a log."""
        return f"{FAMILY_NAMES.get(self.engine, self.engine)} store, storage version {self.storage_version}"


def parse_header(header: bytes) -> Optional[Tuple[str, int]]:
    """
    ``(engine, storage_version)``, or None when these are not an embedded store's
    first bytes.

    Kept separate from the file handling so an upload can be checked from the first
    chunk it streams, before anything has been written to disk.
    """
    if len(header) < HEADER_SIZE:
        return None
    engine = MAGIC_TO_ENGINE.get(bytes(header[:4]))
    if engine is None:
        return None
    offset, width = VERSION_FIELD[engine]
    return engine, int.from_bytes(header[offset:offset + width], "little", signed=False)


def header_file_for(path: Path) -> Optional[Path]:
    """
    The file whose first bytes carry the header, for either layout.

    None when the path exists but holds no recognisable catalog -- an empty
    directory, or one holding something else entirely.
    """
    if path.is_file():
        return path
    if path.is_dir():
        for name in CATALOG_FILENAMES:
            candidate = path / name
            if candidate.is_file():
                return candidate
    return None


def read_header(path: Path) -> bytes:
    """The first ``HEADER_SIZE`` bytes, or fewer if the file is shorter."""
    with open(path, "rb") as handle:
        return handle.read(HEADER_SIZE)


def probe_store(path: os.PathLike) -> StoreFingerprint:
    """
    Identify the store at ``path``.

    Raises ``StoreProbeError`` -- with the path in the message -- for anything that
    is not one, rather than letting an engine fail on it later and less clearly.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise StoreProbeError(f"No such database path: {target}")

    header_path = header_file_for(target)
    if header_path is None:
        if target.is_dir():
            # Only Kuzu 0.10 and older ever wrote a directory; Ladybug and LatticeDB
            # are single-file in every release, so this message is right to name Kuzu.
            raise StoreProbeError(
                f"{target} is a directory but holds no "
                f"{' or '.join(CATALOG_FILENAMES)}, so it is not a Kuzu store"
            )
        raise StoreProbeError(f"{target} cannot be read as a database file")

    try:
        header = read_header(header_path)
    except OSError as exc:
        raise StoreProbeError(f"Cannot read {header_path}: {exc}") from exc

    parsed = parse_header(header)
    if parsed is None:
        found = bytes(header[:4])
        expected = ", ".join(repr(magic) for magic in MAGIC_TO_ENGINE)
        raise StoreProbeError(
            f"{header_path} does not start with an embedded-store magic number "
            f"(found {found!r}, expected one of {expected})"
        )

    engine, storage_version = parsed
    return StoreFingerprint(
        engine=engine,
        storage_version=storage_version,
        path=target,
        header_path=header_path,
        layout=LAYOUT_DIRECTORY if target.is_dir() else LAYOUT_FILE,
    )


def probe_store_or_none(path: Optional[os.PathLike]) -> Optional[StoreFingerprint]:
    """``probe_store`` for callers that treat "not a store" as an answer, not a fault."""
    if not path:
        return None
    try:
        return probe_store(path)
    except StoreProbeError:
        return None
