# -*- coding: utf-8 -*-
"""
Storage format to engine release.

The cases that matter are the ones where the naive answer is wrong: collapsing
patch versions without collapsing Kuzu's ancient ``0.0.x`` line, refusing to let
Kuzu fall forward the way Ladybug can, and preferring what was observed over what
was assumed.
"""

from __future__ import annotations

import json

from kineviz_database_proxy.drivers.embedded.version_map import (
    SEED_RELEASES,
    VersionMap,
    is_release,
    load_version_map,
    newest,
    parse_version,
    save_version_map,
    version_line,
)


# -- version arithmetic -----------------------------------------------------


def test_dev_builds_are_not_releases():
    assert is_release("0.19.1")
    assert is_release("0.7.1.1")
    assert not is_release("0.17.0.dev20260520")
    assert not is_release("1.0.0rc1")


def test_versions_sort_numerically_not_lexically():
    assert parse_version("0.10.0") > parse_version("0.9.0")
    assert newest(["0.9.0", "0.10.0", "0.11.3"]) == "0.11.3"
    assert newest(["0.17.0.dev20260520"]) is None


def test_a_line_is_the_first_two_components():
    assert version_line("0.19.1") == "0.19"
    assert version_line("0.11.3") == "0.11"
    assert version_line("0.7.1.1") == "0.7"


# -- lookups ----------------------------------------------------------------


def test_the_seed_places_every_current_release_without_a_network_call():
    version_map = VersionMap()
    assert version_map.writes_of("kuzu", "0.11.3") == 39
    assert version_map.writes_of("ladybug", "0.19.1") == 43
    assert version_map.writes_of("ladybug", "0.18.0") == 42


def test_a_format_resolves_to_the_newest_release_that_writes_it():
    # This is what "ignore the patch version" means: 0.19.0 and 0.19.1 both write
    # 43, and the newest wins without anything having to know they are one line.
    version_map = VersionMap()
    assert version_map.candidates("ladybug", 43)[0] == "0.19.1"
    assert version_map.candidates("kuzu", 39)[0] == "0.11.3"


def test_a_release_published_later_on_the_same_line_wins_once_it_is_known():
    version_map = VersionMap()
    version_map.learn_writes("ladybug", "0.19.2", 43)
    assert version_map.candidates("ladybug", 43)[0] == "0.19.2"


def test_the_ancient_kuzu_line_is_not_collapsed_by_patch_folding():
    # 0.0.8 writes 17 and 0.0.9 writes 23. A resolver keyed on "0.0" would offer
    # either for both.
    version_map = VersionMap()
    assert version_map.candidates("kuzu", 17) == ["0.0.8"]
    assert "0.0.8" not in version_map.candidates("kuzu", 23)


def test_ladybug_may_fall_forward_to_the_nearest_newer_release():
    version_map = VersionMap()
    candidates = version_map.candidates("ladybug", 42)

    # Exact writers first ...
    assert candidates[:2] == ["0.18.1", "0.18.0"]
    # ... then newer releases, nearest above first, because canReadStorageVersion
    # looks backwards from where it stands.
    forward = candidates[2:]
    assert forward[0] == "0.19.1"
    assert forward.index("0.19.1") < forward.index("0.20.0")


def test_kuzu_never_falls_forward():
    # Kuzu compares formats for equality; letting 0.11.3 try a 0.10.0 store turns a
    # refusal into a UnicodeDecodeError out of the catalog reader.
    version_map = VersionMap()
    assert version_map.candidates("kuzu", 38) == ["0.10.0"]


def test_an_observed_open_outranks_the_seeded_guess():
    version_map = VersionMap()
    version_map.learn_reads("ladybug", "0.20.0", 42)
    candidates = version_map.candidates("ladybug", 42)
    assert candidates[0] == "0.20.0"


def test_a_release_that_failed_to_open_a_format_stops_being_offered():
    version_map = VersionMap()
    version_map.learn_reads("ladybug", "0.20.0", 42)
    assert version_map.forget("ladybug", "0.20.0", 42)
    assert version_map.candidates("ladybug", 42)[0] == "0.18.1"


def test_candidates_are_limited_to_what_is_actually_installable():
    version_map = VersionMap()
    available = ["0.18.0", "0.19.0", "0.19.1"]
    assert version_map.candidates("ladybug", 43, available=available) == ["0.19.1", "0.19.0"]
    # 0.18.1 is seeded but not published in this list, so it is not offered.
    assert version_map.candidates("ladybug", 42, available=available)[0] == "0.18.0"


def test_an_unknown_format_yields_no_candidates_rather_than_a_wrong_one():
    version_map = VersionMap()
    assert version_map.candidates("kuzu", 999) == []


# -- discovery --------------------------------------------------------------


def test_discovery_only_probes_releases_newer_than_anything_known():
    version_map = VersionMap()
    available = ["0.18.0", "0.19.1", "0.20.0", "0.21.0", "0.21.1", "0.22.0"]

    probes = version_map.discovery_candidates("ladybug", available)

    assert probes == ["0.22.0", "0.21.1"]
    assert "0.19.1" not in probes  # already mapped
    assert "0.21.0" not in probes  # thinned: one probe per line


def test_discovery_is_bounded():
    version_map = VersionMap()
    available = [f"0.{minor}.0" for minor in range(21, 40)]
    assert len(version_map.discovery_candidates("ladybug", available, limit=3)) == 3


def test_discovery_ignores_dev_builds():
    version_map = VersionMap()
    probes = version_map.discovery_candidates(
        "ladybug", ["0.21.0.dev20260901", "0.21.0"]
    )
    assert probes == ["0.21.0"]


# -- persistence ------------------------------------------------------------


def test_only_learned_facts_are_written_never_the_seed(tmp_path):
    version_map = VersionMap()
    version_map.learn_writes("ladybug", "0.21.0", 48)

    path = tmp_path / "engine_versions.json"
    save_version_map(version_map, path)
    written = json.loads(path.read_text(encoding="utf-8"))

    assert written == {"ladybug": {"0.21.0": {"writes": 48, "reads": [48]}}}
    # Freezing this build's seed into a user's config would stop a later proxy from
    # correcting it.
    assert "0.19.1" not in written.get("ladybug", {})


def test_a_learned_map_survives_a_round_trip(tmp_path):
    original = VersionMap()
    original.learn_writes("ladybug", "0.21.0", 48)
    original.learn_reads("ladybug", "0.21.0", 43)

    path = tmp_path / "engine_versions.json"
    save_version_map(original, path)
    restored = load_version_map(path)

    assert restored.writes_of("ladybug", "0.21.0") == 48
    assert restored.candidates("ladybug", 48) == ["0.21.0"]
    assert restored.candidates("ladybug", 43)[0] == "0.21.0"
    # The seed is still there alongside it.
    assert restored.writes_of("kuzu", "0.11.3") == 39


def test_a_missing_or_corrupt_learned_file_is_not_fatal(tmp_path):
    assert load_version_map(tmp_path / "absent.json").writes_of("kuzu", "0.11.3") == 39

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_version_map(broken).writes_of("kuzu", "0.11.3") == 39


def test_junk_inside_a_learned_file_is_skipped_rather_than_trusted(tmp_path):
    path = tmp_path / "engine_versions.json"
    path.write_text(
        json.dumps(
            {
                "ladybug": {
                    "0.21.0": {"writes": 48, "reads": [48]},
                    "not-a-version": {"writes": 99},
                    "0.22.0": "nonsense",
                },
                "bogus-engine": [],
            }
        ),
        encoding="utf-8",
    )

    restored = load_version_map(path)

    assert restored.writes_of("ladybug", "0.21.0") == 48
    assert restored.writes_of("ladybug", "not-a-version") is None
    assert restored.writes_of("ladybug", "0.22.0") is None


def test_saving_into_an_unwritable_location_does_not_raise(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    # A read-only or occupied config path must not break a query that only wanted
    # to record what it learned.
    save_version_map(VersionMap(), blocker / "nested" / "engine_versions.json")


def test_the_two_families_do_not_share_a_format_number():
    # Ladybug continued Kuzu's counter at 40 rather than restarting it, which is
    # why the format alone is unambiguous even before the magic bytes are read.
    kuzu_formats = set(SEED_RELEASES["kuzu"].values())
    ladybug_formats = set(SEED_RELEASES["ladybug"].values())
    assert not (kuzu_formats & ladybug_formats)


def test_latticedb_resolves_a_format_to_the_releases_that_write_it():
    version_map = VersionMap()

    assert version_map.writes_of("latticedb", "0.14.0") == 3
    assert version_map.writes_of("latticedb", "0.8.7") == 2
    assert version_map.writes_of("latticedb", "0.4.2") == 1

    # Newest writer of the format first, exactly as the other two families resolve.
    assert version_map.candidates("latticedb", 2)[0] == "0.8.7"


def test_latticedb_may_fall_forward_because_a_newer_build_reads_older_stores():
    """
    0.14.0 opens formats 1, 2 and 3; 0.8.7 opens only its own. So a format this map
    has no writer for must still be allowed to reach a newer release, the way
    Ladybug does and Kuzu must not.
    """
    version_map = VersionMap()
    candidates = version_map.candidates("latticedb", 2)

    assert "0.14.0" in candidates
    assert candidates.index("0.8.7") < candidates.index("0.14.0")


def test_latticedb_release_0_2_0_is_never_offered():
    # The one release published as a pure-Python wheel: no liblattice inside it, so
    # installing it could only ever produce an engine that cannot start.
    assert "0.2.0" not in SEED_RELEASES["latticedb"]


def test_latticedb_keeps_its_own_format_counter_and_the_magic_is_what_separates_them():
    """
    Unlike Ladybug, LatticeDB did not continue Kuzu's numbering -- it started at 1,
    so formats 1 through 3 mean one thing under ``KUZU`` and another under ``BDTL``.
    Nothing may key on the format number alone across families.
    """
    latticedb_formats = set(SEED_RELEASES["latticedb"].values())
    kuzu_formats = set(SEED_RELEASES["kuzu"].values())

    assert latticedb_formats & kuzu_formats
