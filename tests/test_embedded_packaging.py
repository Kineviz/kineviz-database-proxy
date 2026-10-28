# -*- coding: utf-8 -*-
"""
Picking a release that will actually install, and remembering that it did.

The filenames in the fixture are the real file list of ``kuzu`` 0.11.3 as published
on 2026-08-27, trimmed to the platforms these cases care about. It is the concrete
example the whole interpreter-selection path exists for: there is no ``cp314``
Windows wheel, so a proxy running CPython 3.14 on Windows has to run the engine
somewhere else or say why it cannot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kineviz_database_proxy.drivers.embedded import runtime as runtime_module
from kineviz_database_proxy.drivers.embedded.pypi import (
    PackageIndex,
    has_compatible_wheel,
    interpreters_with_a_wheel,
    parse_simple_index,
    supported_tags,
)
from kineviz_database_proxy.drivers.embedded.runtime import (
    EngineRuntime,
    installed_runtime,
    remove_runtime,
    runtime_root,
)

KUZU_FILES = [
    "kuzu-0.11.3-cp310-cp310-win_amd64.whl",
    "kuzu-0.11.3-cp311-cp311-win_amd64.whl",
    "kuzu-0.11.3-cp312-cp312-win_amd64.whl",
    "kuzu-0.11.3-cp313-cp313-win_amd64.whl",
    "kuzu-0.11.3-cp313-cp313-macosx_11_0_arm64.whl",
    "kuzu-0.11.3-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
    # cp314 exists, but only for Linux -- which is the whole point.
    "kuzu-0.11.3-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
    "kuzu-0.11.3.tar.gz",
    "kuzu-0.10.0-cp312-cp312-win_amd64.whl",
]


def index_payload(files=KUZU_FILES, versions=None, yanked=()):
    return {
        "name": "kuzu",
        "versions": versions if versions is not None else ["0.10.0", "0.11.3"],
        "files": [{"filename": name, "yanked": name in yanked} for name in files],
    }


# -- index parsing ----------------------------------------------------------


def test_wheel_tags_are_collected_per_version():
    index = parse_simple_index("kuzu", index_payload())

    tags = index.tags_for("0.11.3")
    assert "cp313-cp313-win_amd64" in tags
    assert "cp314-cp314-manylinux_2_27_x86_64" in tags
    assert index.tags_for("0.10.0") == {"cp312-cp312-win_amd64"}


def test_a_source_distribution_is_recorded_but_is_not_a_wheel():
    index = parse_simple_index("kuzu", index_payload())
    assert index.has_sdist.get("0.11.3") is True
    assert not any("tar.gz" in tag for tag in index.tags_for("0.11.3"))


def test_a_compressed_tag_set_is_expanded():
    index = parse_simple_index(
        "demo", {"versions": ["1.0"], "files": [{"filename": "demo-1.0-py2.py3-none-any.whl"}]}
    )
    assert index.tags_for("1.0") == {"py2-none-any", "py3-none-any"}


def test_a_yanked_file_is_ignored():
    # Installing a release the publisher withdrew, on a user's behalf, is picking up
    # something they pulled on purpose.
    index = parse_simple_index(
        "kuzu", index_payload(versions=[], yanked={"kuzu-0.11.3-cp313-cp313-win_amd64.whl"})
    )
    assert "cp313-cp313-win_amd64" not in index.tags_for("0.11.3")


def test_an_unparseable_filename_does_not_break_the_index():
    index = parse_simple_index(
        "kuzu", {"versions": ["0.11.3"], "files": [{"filename": "garbage.whl"}, {"filename": "x"}]}
    )
    assert index.versions == ["0.11.3"]


# -- interpreter selection --------------------------------------------------


def test_the_running_interpreters_tags_are_offered():
    tags = supported_tags()
    assert any(f"cp3{sys.version_info.minor}" in tag for tag in tags)


def test_a_wheel_can_be_checked_against_an_interpreter_that_is_not_running():
    index = parse_simple_index("kuzu", index_payload())
    # Asking about 3.12 must not depend on 3.12 being installed: the whole point is
    # to answer "would it have worked?" before provisioning anything.
    usable = interpreters_with_a_wheel(index, "0.11.3")
    assert usable  # some interpreter on this platform can take it
    assert all(isinstance(minor, int) for minor in usable)


def test_a_release_with_no_wheel_for_this_platform_is_recognised():
    index = parse_simple_index(
        "kuzu",
        {
            "versions": ["9.9.9"],
            "files": [{"filename": "kuzu-9.9.9-cp313-cp313-someothervariant_riscv64.whl"}],
        },
    )
    assert not has_compatible_wheel(index, "9.9.9")
    assert interpreters_with_a_wheel(index, "9.9.9") == []


def test_the_interpreter_plan_always_offers_a_fallback_after_the_running_one():
    index = parse_simple_index("kuzu", index_payload())
    plan = runtime_module._interpreter_plan("kuzu", "0.11.3", index)

    assert plan, "a release with wheels must have somewhere to run"
    # Every fallback is a uv-managed interpreter, because a wheel matching the tags
    # can still fail to import against a host CPython built with different
    # dependency names -- which is exactly how the Ladybug wheel behaves on Windows.
    assert all(choice.managed_only for choice in plan[1:])


def test_offline_the_plan_falls_back_to_this_interpreter_alone():
    plan = runtime_module._interpreter_plan("kuzu", "0.11.3", None)
    assert [choice.spec for choice in plan] == [sys.executable]


def test_the_wheel_gap_message_names_interpreters_that_would_work():
    index = parse_simple_index("kuzu", index_payload())
    message = runtime_module._describe_wheel_gap("kuzu", "0.11.3", index)
    assert "kuzu 0.11.3" in message
    assert "3." in message


def test_the_wheel_gap_message_says_so_when_nothing_would_work():
    index = parse_simple_index(
        "kuzu", {"versions": ["9.9.9"], "files": [{"filename": "kuzu-9.9.9.tar.gz"}]}
    )
    message = runtime_module._describe_wheel_gap("kuzu", "9.9.9", index)
    assert "no wheel for this platform at all" in message


def test_the_wheel_gap_message_names_the_platforms_that_do_have_wheels():
    """
    LatticeDB's real shape: macOS and manylinux wheels, no Windows wheel for any
    interpreter. The gap is the platform rather than the Python version, so naming
    another interpreter would send the user somewhere that cannot help them.
    """
    index = parse_simple_index(
        "latticedb",
        {
            "versions": ["0.14.0"],
            "files": [
                {"filename": "latticedb-0.14.0-py3-none-macosx_11_0_arm64.whl"},
                {"filename": "latticedb-0.14.0-py3-none-manylinux_2_17_x86_64.whl"},
                {"filename": "latticedb-0.14.0-py3-none-manylinux_2_17_aarch64.whl"},
                {"filename": "latticedb-0.14.0.tar.gz"},
            ],
        },
    )

    assert runtime_module._wheel_platforms(index, "0.14.0") == ["macosx", "manylinux"]

    if sys.platform == "win32":
        message = runtime_module._describe_wheel_gap("latticedb", "0.14.0", index)
        assert "no wheel for this platform at all" in message
        assert "macosx, manylinux" in message


# -- the install marker -----------------------------------------------------


@pytest.fixture
def engines_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHXR_PROXY_ENGINES_DIR", str(tmp_path / "engines"))
    return tmp_path / "engines"


def write_marker(engine, version, python, storage_version=None, site_dir=None):
    root = runtime_root(engine, version)
    root.mkdir(parents=True, exist_ok=True)
    (root / "runtime.json").write_text(
        json.dumps(
            {
                "engine": engine,
                "version": version,
                "python": str(python),
                "site_dir": str(site_dir) if site_dir else None,
                "storage_version": storage_version,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_an_installed_release_is_found_through_its_marker(engines_dir):
    write_marker("kuzu", "0.11.3", sys.executable, storage_version=39)

    found = installed_runtime("kuzu", "0.11.3")

    assert found is not None
    assert found.python == Path(sys.executable)
    assert found.storage_version == 39


def test_a_marker_pointing_at_a_vanished_interpreter_is_not_an_install(engines_dir):
    # A uv-managed Python that was cleaned up, or a home directory copied between
    # machines. Reinstalling beats failing at the user's first query.
    write_marker("kuzu", "0.11.3", engines_dir / "gone" / "python.exe")
    assert installed_runtime("kuzu", "0.11.3") is None


def test_a_target_install_whose_site_directory_vanished_is_not_an_install(engines_dir):
    write_marker("kuzu", "0.11.3", sys.executable, site_dir=engines_dir / "missing-site")
    assert installed_runtime("kuzu", "0.11.3") is None


def test_a_missing_or_corrupt_marker_is_not_an_install(engines_dir):
    assert installed_runtime("kuzu", "0.11.3") is None
    root = runtime_root("kuzu", "0.11.3")
    root.mkdir(parents=True)
    (root / "runtime.json").write_text("{oops", encoding="utf-8")
    assert installed_runtime("kuzu", "0.11.3") is None


def test_removing_a_runtime_takes_the_marker_with_it(engines_dir):
    write_marker("kuzu", "0.11.3", sys.executable)
    assert remove_runtime("kuzu", "0.11.3")
    assert installed_runtime("kuzu", "0.11.3") is None
    assert not remove_runtime("kuzu", "0.11.3")


def test_a_target_install_puts_its_site_directory_on_the_child_pythonpath(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    runtime = EngineRuntime(
        engine="kuzu", version="0.11.3", root=tmp_path, python=Path(sys.executable), site_dir=site
    )
    assert runtime.env()["PYTHONPATH"].startswith(str(site))


def test_a_venv_install_leaves_pythonpath_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    runtime = EngineRuntime(
        engine="kuzu", version="0.11.3", root=tmp_path, python=Path(sys.executable)
    )
    assert runtime.env()["PYTHONPATH"] == "/somewhere/else"
