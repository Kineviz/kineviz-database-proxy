# -*- coding: utf-8 -*-
"""
The header probe, over every magic and both on-disk layouts.

The byte patterns here are not invented: they are the first twelve bytes of stores
created by kuzu 0.11.3, kuzu 0.8.0 and ladybug 0.19.1 on 2026-08-27, and by
latticedb 0.4.2, 0.8.7 and 0.14.0 on 2026-08-28.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.drivers.embedded.store_probe import (
    KUZU_MAGIC,
    LADYBUG_MAGIC,
    LATTICE_MAGIC,
    LAYOUT_DIRECTORY,
    LAYOUT_FILE,
    StoreProbeError,
    parse_header,
    probe_store,
    probe_store_or_none,
)


def header(magic: bytes, storage_version: int) -> bytes:
    return magic + storage_version.to_bytes(8, "little")


#: Verbatim from a store written by kuzu 0.11.3.
KUZU_0_11_HEADER = bytes.fromhex("4b555a5527000000000000000c020000")
#: Verbatim from a store written by ladybug 0.19.1.
LADYBUG_0_19_HEADER = bytes.fromhex("4c4255472b0000000000000014000000")
#: Verbatim from stores written by latticedb 0.14.0, 0.8.7 and 0.4.2. The format
#: number is a uint16 and appears twice -- the write version and the oldest reader
#: allowed -- and the ``0010`` that follows is the 4096-byte page size.
LATTICE_0_14_HEADER = bytes.fromhex("4244544c03000300001000000800000000000000")
LATTICE_0_8_HEADER = bytes.fromhex("4244544c02000200001000000800000000000000")
LATTICE_0_4_HEADER = bytes.fromhex("4244544c01000100001000000800000000000000")


def test_the_recorded_kuzu_header_reads_as_storage_version_39():
    assert parse_header(KUZU_0_11_HEADER) == ("kuzu", 39)


def test_the_recorded_ladybug_header_reads_as_storage_version_43():
    assert parse_header(LADYBUG_0_19_HEADER) == ("ladybug", 43)


def test_the_version_is_read_as_a_full_uint64_not_the_byte_after_the_magic():
    # Identical in the low byte, different in the second: a one-byte read would call
    # these the same format.
    assert parse_header(header(KUZU_MAGIC, 39)) == ("kuzu", 39)
    assert parse_header(header(KUZU_MAGIC, 39 + 256)) == ("kuzu", 295)


def test_the_recorded_latticedb_headers_read_as_their_format_numbers():
    assert parse_header(LATTICE_0_14_HEADER) == ("latticedb", 3)
    assert parse_header(LATTICE_0_8_HEADER) == ("latticedb", 2)
    assert parse_header(LATTICE_0_4_HEADER) == ("latticedb", 1)


def test_the_latticedb_version_is_a_uint16_not_the_uint64_the_others_use():
    """
    The bytes after LatticeDB's format number are the page size, not more of the
    version. Reading this header the way a Kuzu one is read gives 17592186109955.
    """
    assert parse_header(LATTICE_0_14_HEADER) == ("latticedb", 3)
    assert int.from_bytes(LATTICE_0_14_HEADER[4:12], "little") != 3


def test_a_latticedb_file_probes_as_a_single_file_store(tmp_path):
    store = tmp_path / "knowledge.db"
    store.write_bytes(LATTICE_0_14_HEADER + bytes(64))

    fingerprint = probe_store(store)

    assert fingerprint.engine == "latticedb"
    assert fingerprint.storage_version == 3
    assert fingerprint.layout == LAYOUT_FILE
    assert fingerprint.path == store
    assert "LatticeDB" in fingerprint.describe()


def test_a_foreign_or_short_header_is_not_a_store():
    assert parse_header(b"SQLite format 3\x00") is None
    assert parse_header(b"KUZU") is None
    assert parse_header(b"") is None


def test_a_single_file_store_is_probed_directly(tmp_path):
    store = tmp_path / "graph.kz"
    store.write_bytes(LADYBUG_0_19_HEADER + b"\x00" * 64)

    fingerprint = probe_store(store)

    assert (fingerprint.engine, fingerprint.storage_version) == ("ladybug", 43)
    assert fingerprint.layout == LAYOUT_FILE
    assert fingerprint.path == store
    assert fingerprint.header_path == store


def test_a_directory_store_is_probed_through_its_catalog(tmp_path):
    # Kuzu 0.10 and older: the directory is the database, and data.kz is empty in a
    # fresh store, so only catalog.kz carries the magic.
    store = tmp_path / "olddb"
    store.mkdir()
    (store / "catalog.kz").write_bytes(header(KUZU_MAGIC, 36))
    (store / "data.kz").write_bytes(b"")
    (store / "metadata.kz").write_bytes(b"\x01\x00\x00\x00")

    fingerprint = probe_store(store)

    assert (fingerprint.engine, fingerprint.storage_version) == ("kuzu", 36)
    assert fingerprint.layout == LAYOUT_DIRECTORY
    # The engine is handed the directory, but the header came from inside it.
    assert fingerprint.path == store
    assert fingerprint.header_path == store / "catalog.kz"


def test_the_older_catalog_spelling_is_also_recognised(tmp_path):
    store = tmp_path / "ancient"
    store.mkdir()
    (store / "catalog.bin").write_bytes(header(KUZU_MAGIC, 23))

    assert probe_store(store).storage_version == 23


def test_a_missing_path_names_itself_in_the_error(tmp_path):
    missing = tmp_path / "nope.kz"
    with pytest.raises(StoreProbeError, match="nope.kz"):
        probe_store(missing)


def test_a_directory_without_a_catalog_says_so(tmp_path):
    store = tmp_path / "empty"
    store.mkdir()
    with pytest.raises(StoreProbeError, match="catalog.kz"):
        probe_store(store)


def test_a_file_that_is_not_a_store_reports_what_it_found_instead(tmp_path):
    store = tmp_path / "notes.txt"
    store.write_text("hello there, this is not a graph database", encoding="utf-8")

    with pytest.raises(StoreProbeError) as error:
        probe_store(store)

    message = str(error.value)
    assert "magic number" in message
    assert repr(KUZU_MAGIC) in message and repr(LADYBUG_MAGIC) in message


def test_probe_or_none_turns_every_failure_into_an_answer(tmp_path):
    assert probe_store_or_none(None) is None
    assert probe_store_or_none(tmp_path / "missing") is None

    store = tmp_path / "graph.kz"
    store.write_bytes(KUZU_0_11_HEADER)
    assert probe_store_or_none(store).engine == "kuzu"
