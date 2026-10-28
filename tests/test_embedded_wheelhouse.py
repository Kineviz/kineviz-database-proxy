# -*- coding: utf-8 -*-
"""
A wheel built on this machine, offered to the installer like a published one.

The case these exist for is LatticeDB on Windows: the engine cross-compiles for it
in one command, and PyPI has nothing to install because the upstream CI has no
Windows job. Everything here is about that gap being closable by the user without
the proxy having to special-case a platform.
"""

from __future__ import annotations

import sys

import pytest

from kineviz_database_proxy.drivers.embedded import runtime as runtime_module
from kineviz_database_proxy.drivers.embedded.pypi import (
    PackageIndex,
    PackageIndexError,
    parse_simple_index,
    supported_tags,
)
from kineviz_database_proxy.drivers.embedded.wheelhouse import (
    local_index,
    local_wheels,
    merged_index,
    wheel_for,
    wheelhouse_dir,
)

#: Tagged for every interpreter on every platform, which is what a pure-Python
#: package bundling a shared library actually is once the library is inside it.
PORTABLE = "latticedb-0.14.0-py3-none-any.whl"

#: A platform this test is certainly not running on, so "usable here" can be tested
#: without knowing which machine is running it.
FOREIGN = "latticedb-0.14.0-cp313-cp313-someothervariant_riscv64.whl"


@pytest.fixture
def wheelhouse(tmp_path, monkeypatch):
    root = tmp_path / "wheelhouse"
    root.mkdir()
    monkeypatch.setenv("GRAPHXR_PROXY_WHEELHOUSE", str(root))
    return root


def put(root, name):
    path = root / name
    path.write_bytes(b"not really a zip, and nothing here opens it")
    return path


# -- finding wheels ---------------------------------------------------------


def test_the_wheelhouse_is_overridden_by_environment(wheelhouse):
    assert wheelhouse_dir() == wheelhouse


def test_a_missing_wheelhouse_is_empty_rather_than_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHXR_PROXY_WHEELHOUSE", str(tmp_path / "never-created"))
    assert list(local_wheels("latticedb")) == []
    assert local_index("latticedb").versions == []


def test_only_wheels_for_the_engine_asked_about_are_offered(wheelhouse):
    put(wheelhouse, PORTABLE)
    put(wheelhouse, "kuzu-0.11.3-cp313-cp313-win_amd64.whl")
    put(wheelhouse, "notes.txt")
    put(wheelhouse, "garbage.whl")

    assert [version for version, _ in local_wheels("latticedb")] == ["0.14.0"]
    assert [version for version, _ in local_wheels("kuzu")] == ["0.11.3"]


def test_package_names_are_compared_the_way_pep_503_compares_them(wheelhouse):
    put(wheelhouse, "Lattice_DB-0.14.0-py3-none-any.whl")
    assert [version for version, _ in local_wheels("lattice-db")] == ["0.14.0"]


def test_the_index_carries_the_tags_the_filename_declares(wheelhouse):
    put(wheelhouse, PORTABLE)
    put(wheelhouse, "latticedb-0.13.0-py3-none-win_amd64.whl")

    index = local_index("latticedb")

    assert sorted(index.versions) == ["0.13.0", "0.14.0"]
    assert index.tags_for("0.14.0") == {"py3-none-any"}
    assert index.tags_for("0.13.0") == {"py3-none-win_amd64"}


# -- choosing one -----------------------------------------------------------


def test_a_wheel_this_machine_can_load_is_offered_by_path(wheelhouse):
    expected = put(wheelhouse, PORTABLE)
    assert wheel_for("latticedb", "0.14.0") == expected


def test_a_wheel_built_for_another_machine_is_not_offered(wheelhouse):
    # A wheelhouse shared between a laptop and a build server holds files for both.
    # Handing pip the wrong one turns a clear refusal into an install that succeeds
    # and then cannot import.
    put(wheelhouse, FOREIGN)
    assert "cp313-cp313-someothervariant_riscv64" not in supported_tags()
    assert wheel_for("latticedb", "0.14.0") is None


def test_a_version_the_wheelhouse_does_not_have_is_not_invented(wheelhouse):
    put(wheelhouse, PORTABLE)
    assert wheel_for("latticedb", "0.13.0") is None


# -- merging with the index -------------------------------------------------


def test_merging_leaves_the_published_index_untouched():
    published = parse_simple_index(
        "latticedb",
        {
            "versions": ["0.14.0"],
            "files": [{"filename": "latticedb-0.14.0-py3-none-manylinux_2_17_x86_64.whl"}],
        },
    )
    local = PackageIndex(package="latticedb", versions=["0.14.0"])
    local.wheel_tags["0.14.0"] = {"py3-none-win_amd64"}

    merged = merged_index(published, local)

    # The published index is a shared cache entry; folding local tags into it in
    # place would leak this machine's build into every later lookup.
    assert published.tags_for("0.14.0") == {"py3-none-manylinux_2_17_x86_64"}
    assert merged.tags_for("0.14.0") == {
        "py3-none-manylinux_2_17_x86_64",
        "py3-none-win_amd64",
    }


def test_a_release_only_the_wheelhouse_has_joins_the_versions():
    published = parse_simple_index("latticedb", {"versions": ["0.14.0"], "files": []})
    local = PackageIndex(package="latticedb", versions=["0.15.0"])
    local.wheel_tags["0.15.0"] = {"py3-none-any"}

    merged = merged_index(published, local)

    assert sorted(merged.versions) == ["0.14.0", "0.15.0"]


# -- what the installer does with it ----------------------------------------


def test_the_installer_is_given_a_path_when_the_wheel_is_local(wheelhouse):
    expected = put(wheelhouse, PORTABLE)
    assert runtime_module._requirement("latticedb", "0.14.0") == str(expected)


def test_the_installer_is_given_a_requirement_when_it_is_not(wheelhouse):
    assert runtime_module._requirement("latticedb", "0.14.0") == "latticedb==0.14.0"


class _FakeIndexCache:
    def __init__(self, index=None, error=None):
        self.index = index
        self.error = error

    async def get(self, package, refresh=False):
        if self.error is not None:
            raise self.error
        return self.index


async def test_a_local_wheel_is_folded_into_what_the_index_reports(wheelhouse, monkeypatch):
    put(wheelhouse, "latticedb-0.14.0-py3-none-win_amd64.whl")
    published = parse_simple_index(
        "latticedb",
        {
            "versions": ["0.14.0"],
            "files": [{"filename": "latticedb-0.14.0-py3-none-manylinux_2_17_x86_64.whl"}],
        },
    )
    monkeypatch.setattr(runtime_module, "PACKAGE_INDEX", _FakeIndexCache(published))

    index = await runtime_module._index_for("latticedb")

    assert index is not None
    assert "py3-none-win_amd64" in index.tags_for("0.14.0")


async def test_the_wheelhouse_answers_when_the_index_cannot(wheelhouse, monkeypatch):
    put(wheelhouse, PORTABLE)
    monkeypatch.setattr(
        runtime_module, "PACKAGE_INDEX", _FakeIndexCache(error=PackageIndexError("offline"))
    )

    index = await runtime_module._index_for("latticedb")

    assert index is not None
    assert index.tags_for("0.14.0") == {"py3-none-any"}


async def test_offline_with_no_local_wheel_is_still_no_index(wheelhouse, monkeypatch):
    monkeypatch.setattr(
        runtime_module, "PACKAGE_INDEX", _FakeIndexCache(error=PackageIndexError("offline"))
    )
    assert await runtime_module._index_for("latticedb") is None


@pytest.mark.skipif(sys.platform != "win32", reason="the gap being closed is Windows-shaped")
def test_a_locally_built_wheel_gives_windows_somewhere_to_install(wheelhouse):
    """
    The whole point, stated as a test: LatticeDB publishes no Windows wheel, so the
    interpreter plan is empty and the install is refused with a platform message.
    A wheel in the wheelhouse turns that back into an ordinary install.
    """
    published = parse_simple_index(
        "latticedb",
        {
            "versions": ["0.14.0"],
            "files": [
                {"filename": "latticedb-0.14.0-py3-none-macosx_11_0_arm64.whl"},
                {"filename": "latticedb-0.14.0-py3-none-manylinux_2_17_x86_64.whl"},
            ],
        },
    )
    assert runtime_module._interpreter_plan("latticedb", "0.14.0", published) == []

    put(wheelhouse, "latticedb-0.14.0-py3-none-win_amd64.whl")
    merged = merged_index(published, local_index("latticedb"))

    assert runtime_module._interpreter_plan("latticedb", "0.14.0", merged)


def test_the_platform_gap_message_points_at_the_wheelhouse(wheelhouse):
    index = parse_simple_index(
        "latticedb",
        {"versions": ["0.14.0"], "files": [{"filename": "latticedb-0.14.0.tar.gz"}]},
    )
    message = runtime_module._describe_wheel_gap("latticedb", "0.14.0", index)
    assert str(wheelhouse) in message
