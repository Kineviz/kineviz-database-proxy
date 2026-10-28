# -*- coding: utf-8 -*-
"""
The store library: what the walk reports, and what the delete refuses.

The paging tests are the point of the module rather than a detail of it. A file
manager that enumerated everything and sliced would work perfectly on a developer's
three test files and fall over on a real library, and no assertion about the
contents of page two would catch that -- so the cost is asserted directly, by
counting how many files are actually opened to build a page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    Project,
)
from kineviz_database_proxy.services import store_library

KUZU_HEADER = b"KUZU" + (39).to_bytes(8, "little")
LADYBUG_HEADER = b"LBUG" + (43).to_bytes(8, "little")
SQLITE_HEADER = b"SQLite format 3\x00"
DUCKDB_HEADER = b"\x00" * 8 + b"DUCK"


def write_store(directory: Path, name: str, header: bytes = KUZU_HEADER) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(header + b"\x00" * 32)
    return path


def make_project(name: str, path: str, database_type=DatabaseType.KUZU) -> Project:
    return Project(
        id=name,
        name=name,
        database_type=database_type,
        database_config=DatabaseConfig(
            type=database_type,
            auth_type=AuthType.USERNAME_PASSWORD,
            database_path=path,
        ),
    )


# -- what the walk finds ----------------------------------------------------


def test_the_walk_is_stable_and_reaches_into_folders(tmp_path):
    write_store(tmp_path / "b", "second.kz")
    write_store(tmp_path, "top.kz")
    write_store(tmp_path / "a", "first.kz")

    found = [p.relative_to(tmp_path).as_posix() for p in store_library.iter_paths(tmp_path)]
    assert found == ["top.kz", "a/first.kz", "b/second.kz"]
    assert found == [p.relative_to(tmp_path).as_posix() for p in store_library.iter_paths(tmp_path)]


def test_a_directory_store_is_one_entry_rather_than_a_folder_of_them(tmp_path):
    # Kuzu 0.10 and older wrote a directory. Descending into it would report its
    # catalog and index files as if each were a store of its own.
    old = tmp_path / "legacy.kuzu"
    write_store(old, "catalog.kz")
    (old / "data.kz").write_bytes(b"")

    found = [p.name for p in store_library.iter_paths(tmp_path)]
    assert found == ["legacy.kuzu"]

    entry = store_library.describe(tmp_path, old)
    assert entry.layout == "directory"
    assert (entry.engine, entry.storage_version) == ("kuzu", 39)


def test_partial_uploads_and_dotfiles_are_not_reported(tmp_path):
    write_store(tmp_path, "real.kz")
    (tmp_path / ".real.kz.part").write_bytes(KUZU_HEADER)

    assert [p.name for p in store_library.iter_paths(tmp_path)] == ["real.kz"]


def test_search_narrows_the_walk_itself(tmp_path):
    write_store(tmp_path, "customers.kz")
    write_store(tmp_path, "orders.kz")

    assert [p.name for p in store_library.iter_paths(tmp_path, "order")] == ["orders.kz"]
    assert store_library.count_paths(tmp_path, "order") == 1


def test_a_missing_library_is_empty_rather_than_an_error(tmp_path):
    absent = tmp_path / "never-created"
    assert list(store_library.iter_paths(absent)) == []
    assert store_library.count_paths(absent) == 0
    assert store_library.folders(absent) == []


def test_folders_lists_containers_and_not_directory_stores(tmp_path):
    write_store(tmp_path / "customers", "graph.kz")
    write_store(tmp_path / "legacy.kuzu", "catalog.kz")

    assert store_library.folders(tmp_path) == ["customers"]


# -- paging -----------------------------------------------------------------


def test_a_page_only_opens_the_files_on_it(tmp_path, monkeypatch):
    for index in range(30):
        write_store(tmp_path, f"store-{index:02d}.kz")

    opened = []
    real = store_library._read_prefix
    monkeypatch.setattr(
        store_library,
        "_read_prefix",
        lambda path: (opened.append(path), real(path))[1],
    )

    entries = store_library.page(tmp_path, offset=20, limit=5)

    assert [e.name for e in entries] == [f"store-{i}.kz" for i in range(20, 25)]
    # Twenty skipped and five past the page never reach the disk.
    assert len(opened) == 5


def test_pages_partition_the_library(tmp_path):
    for index in range(7):
        write_store(tmp_path, f"store-{index}.kz")

    seen = []
    for offset in range(0, 9, 3):
        seen.extend(e.relative_path for e in store_library.page(tmp_path, offset, 3))

    assert seen == [f"store-{i}.kz" for i in range(7)]
    assert store_library.count_paths(tmp_path) == 7


# -- identifying ------------------------------------------------------------


@pytest.mark.parametrize(
    "header,kind,engine,storage_version",
    [
        (KUZU_HEADER, "kuzu", "kuzu", 39),
        (LADYBUG_HEADER, "ladybug", "ladybug", 43),
        (SQLITE_HEADER, "sqlite", None, None),
        (DUCKDB_HEADER, "duckdb", None, None),
        (b"not a database at all", "unknown", None, None),
    ],
)
def test_a_file_is_named_by_its_first_bytes(tmp_path, header, kind, engine, storage_version):
    path = write_store(tmp_path, "thing.bin", header)
    entry = store_library.describe(tmp_path, path)

    assert (entry.kind, entry.engine, entry.storage_version) == (kind, engine, storage_version)
    assert entry.servable is (engine is not None)
    assert entry.description


def test_an_unreadable_file_is_still_listed(tmp_path):
    # Being unable to read a file is not a reason to pretend it is not there --
    # it is very likely the reason the user came to the page.
    path = write_store(tmp_path, "empty.kz", b"")
    entry = store_library.describe(tmp_path, path)
    assert entry.kind == "unknown"
    assert entry.name == "empty.kz"


# -- who is using what ------------------------------------------------------


def test_usage_matches_the_same_file_spelled_differently(tmp_path):
    path = write_store(tmp_path, "graph.kz")
    for spelling in (str(path), path.as_posix(), f"{path.parent}/./{path.name}"):
        index = store_library.usage_index([make_project("live", spelling)])

        assert store_library.used_by(index, str(path)) == ["live"]
        assert store_library.used_by(index, str(tmp_path / "other.kz")) == []


def test_external_paths_are_the_ones_outside_the_library(tmp_path):
    inside = write_store(tmp_path, "graph.kz")
    outside = tmp_path.parent / "elsewhere.kz"
    outside.write_bytes(KUZU_HEADER)

    external = store_library.external_paths(
        [make_project("in", str(inside)), make_project("out", str(outside), DatabaseType.LADYBUG)],
        tmp_path,
    )

    assert len(external) == 1
    path, database_type, names = external[0]
    assert Path(path) == outside
    # The route the project is served on, not the enum's repr.
    assert database_type == "ladybug"
    assert list(names) == ["out"]


# -- deleting ---------------------------------------------------------------


def test_a_delete_takes_the_emptied_folder_with_it(tmp_path):
    path = write_store(tmp_path / "customers", "graph.kz")

    store_library.remove(tmp_path, "customers/graph.kz")

    assert not path.exists()
    assert not (tmp_path / "customers").exists()
    assert tmp_path.exists()


def test_a_folder_holding_other_stores_survives(tmp_path):
    write_store(tmp_path / "customers", "one.kz")
    write_store(tmp_path / "customers", "two.kz")

    store_library.remove(tmp_path, "customers/one.kz")

    assert (tmp_path / "customers" / "two.kz").exists()


def test_a_directory_store_is_deleted_whole(tmp_path):
    old = tmp_path / "legacy.kuzu"
    write_store(old, "catalog.kz")

    store_library.remove(tmp_path, "legacy.kuzu")

    assert not old.exists()


def test_a_plain_folder_cannot_be_deleted_by_naming_it(tmp_path):
    write_store(tmp_path / "customers", "graph.kz")

    with pytest.raises(store_library.StoreLibraryError):
        store_library.remove(tmp_path, "customers")

    assert (tmp_path / "customers" / "graph.kz").exists()


@pytest.mark.parametrize("escape", ["../outside.kz", "..", "a/../../outside.kz", ""])
def test_a_path_that_leaves_the_library_is_refused(tmp_path, escape):
    (tmp_path.parent / "outside.kz").write_bytes(KUZU_HEADER)

    with pytest.raises(store_library.StoreLibraryError):
        store_library.resolve(tmp_path, escape)

    assert (tmp_path.parent / "outside.kz").exists()


@pytest.mark.parametrize(
    "hostile",
    [
        "/etc/passwd",
        "C:/Windows/system.ini",
        "\\server\share\graph.kz",
        "....//outside.kz",
        "sub/./../../outside.kz",
    ],
)
def test_no_spelling_of_a_path_escapes_the_library(tmp_path, hostile):
    # Two acceptable answers and no third: refused, or landed inside the root.
    # Written this way rather than as "must raise" because the safe outcome differs
    # by platform -- a leading slash is stripped and stays inside, while a drive
    # letter replaces the root outright and has to be caught.
    try:
        resolved = store_library.resolve(tmp_path, hostile)
    except store_library.StoreLibraryError:
        return
    assert tmp_path.resolve() in resolved.parents


def test_deleting_something_that_is_not_there_says_so(tmp_path):
    with pytest.raises(store_library.StoreLibraryError):
        store_library.remove(tmp_path, "ghost.kz")
