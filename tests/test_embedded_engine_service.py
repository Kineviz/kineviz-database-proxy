# -*- coding: utf-8 -*-
"""
Choosing a release, fetching it once, and learning from what happened.

Installs and engine processes are stubbed here -- they have their own tests, and a
real one would put a 60MB download in the acceptance gate. What is under test is
the decision-making around them: which release is tried, how often it is fetched,
and what the map is told afterwards.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kineviz_database_proxy.drivers.embedded import engine_service as service_module
from kineviz_database_proxy.drivers.embedded.engine_service import (
    STATUS_FAILED,
    STATUS_INSTALLING,
    STATUS_READY,
    EngineResolutionError,
    EngineService,
)
from kineviz_database_proxy.drivers.embedded.pool import EngineWorkerError
from kineviz_database_proxy.drivers.embedded.runtime import EngineInstallError, EngineRuntime
from kineviz_database_proxy.drivers.embedded.store_probe import (
    LAYOUT_FILE,
    StoreFingerprint,
)
from kineviz_database_proxy.drivers.embedded.version_map import VersionMap


def fingerprint(engine="ladybug", storage_version=43, path=Path("/tmp/graph.kz")):
    return StoreFingerprint(
        engine=engine,
        storage_version=storage_version,
        path=path,
        header_path=path,
        layout=LAYOUT_FILE,
    )


class FakeWorker:
    """Stands in for a subprocess: records what it was asked, answers plausibly."""

    def __init__(self, runtime, storage_version=None):
        self.runtime = runtime
        self._storage_version = storage_version
        self.info_calls = 0

    async def start(self):
        return None

    async def stop(self):
        return None

    async def info(self):
        self.info_calls += 1
        return {"version": self.runtime.version, "storage_version": self._storage_version}


class FakePool:
    """A pool that opens whatever it is handed, unless told to refuse a version."""

    def __init__(self, refuse=()):
        self.refuse = set(refuse)
        self.acquired = []

    async def acquire(self, runtime, path, read_only=True):
        self.acquired.append((runtime.version, path, read_only))
        if runtime.version in self.refuse:
            raise EngineWorkerError("Unable to open database. The file is not valid!")
        return FakeWorker(runtime, storage_version=None)


@pytest.fixture
def offline_map(tmp_path, monkeypatch):
    """Keep the learned map inside the test, and never reach PyPI."""
    monkeypatch.setenv("GRAPHXR_PROXY_ENGINE_MAP", str(tmp_path / "engine_versions.json"))
    return tmp_path / "engine_versions.json"


@pytest.fixture
def service(offline_map, monkeypatch):
    made = EngineService(VersionMap())

    async def no_releases(_engine):
        return None  # "could not ask", which is what offline means here

    monkeypatch.setattr(made, "available_releases", no_releases)
    return made


def stub_installs(monkeypatch, *, installed=(), fail=(), record=None):
    """Replace the two functions that touch the disk with bookkeeping."""
    present = set(installed)

    def fake_installed_runtime(engine, version):
        if (engine, version) not in present:
            return None
        return EngineRuntime(
            engine=engine, version=version, root=Path("/engines"), python=Path("/python")
        )

    async def fake_install_runtime(engine, version, on_progress=None):
        if record is not None:
            record.append((engine, version))
        if on_progress:
            on_progress("downloading")
        if version in fail:
            raise EngineInstallError(f"no wheel for {engine} {version}")
        await asyncio.sleep(0)
        present.add((engine, version))
        return EngineRuntime(
            engine=engine, version=version, root=Path("/engines"), python=Path("/python")
        )

    monkeypatch.setattr(service_module, "installed_runtime", fake_installed_runtime)
    monkeypatch.setattr(service_module, "install_runtime", fake_install_runtime)
    return present


# -- choosing a release -----------------------------------------------------


async def test_the_newest_release_writing_the_format_is_chosen(service):
    assert (await service.candidates("ladybug", 43))[0] == "0.19.1"


async def test_a_pin_may_name_a_line_rather_than_a_release(service):
    # "0.19" is how a user thinks about a pin, and it must not freeze the project
    # onto one patch release forever.
    assert await service.candidates("ladybug", 43, pin="0.19") == ["0.19.1"]


async def test_a_pin_may_name_an_exact_release(service):
    assert await service.candidates("ladybug", 43, pin="0.19.0") == ["0.19.0"]


async def test_a_pin_wins_over_the_format_derived_answer(service):
    # The user asked for it; the store may still refuse to open, and that error is
    # more useful than silently ignoring the pin.
    assert await service.candidates("ladybug", 42, pin="0.19.1") == ["0.19.1"]


async def test_a_nonsense_pin_is_ignored_rather_than_fatal(service):
    assert (await service.candidates("ladybug", 43, pin="not-a-version"))[0] == "0.19.1"


async def test_an_unknown_older_format_never_triggers_a_download(service, monkeypatch):
    # Discovery only ever looks forward. A format below everything known will not be
    # explained by a newer release, and proving that by downloading wheels would be
    # worse than the clear error the caller gets instead.
    called = []

    async def spy(engine, storage_version, available):
        called.append(storage_version)
        return []

    monkeypatch.setattr(service, "_discover", spy)
    assert await service.candidates("ladybug", 41) == ["0.17.1", "0.17.0", "0.18.1", "0.18.0", "0.19.1", "0.19.0", "0.20.0"]
    assert called == []


async def test_an_unknown_newer_format_probes_releases_it_has_never_seen(
    service, monkeypatch
):
    async def releases(_engine):
        return ["0.19.1", "0.20.0", "0.21.0"]

    monkeypatch.setattr(service, "available_releases", releases)
    stub_installs(monkeypatch)

    async def interrogate(runtime):
        # 0.21.0 turns out to write the format nobody had heard of.
        written = 48 if runtime.version == "0.21.0" else 47
        service.version_map.learn_writes(runtime.engine, runtime.version, written)
        return written

    monkeypatch.setattr(service, "_interrogate", interrogate)

    assert await service.candidates("ladybug", 48) == ["0.21.0"]
    # And it stays known, without another download.
    assert service.version_map.writes_of("ladybug", "0.21.0") == 48


# -- installing -------------------------------------------------------------


async def test_an_already_installed_release_is_not_fetched_again(service, monkeypatch):
    record = []
    stub_installs(monkeypatch, installed=[("ladybug", "0.19.1")], record=record)

    await service.ensure("ladybug", "0.19.1")

    assert record == []
    assert service.status("ladybug", "0.19.1").status == STATUS_READY


async def test_concurrent_callers_share_one_install(service, monkeypatch):
    # The eager trigger and a query that lands during the download must not start
    # two of them.
    record = []
    stub_installs(monkeypatch, record=record)

    await asyncio.gather(*(service.ensure("ladybug", "0.19.1") for _ in range(5)))

    assert record == [("ladybug", "0.19.1")]


async def test_a_backgrounded_install_reports_progress_then_ready(service, monkeypatch):
    stub_installs(monkeypatch)

    state = service.start_install("ladybug", "0.19.1")
    assert state.status == STATUS_INSTALLING

    await service.ensure("ladybug", "0.19.1")
    assert service.status("ladybug", "0.19.1").status == STATUS_READY


async def test_a_failed_install_is_reported_with_its_reason(service, monkeypatch):
    stub_installs(monkeypatch, fail={"0.19.1"})

    with pytest.raises(EngineInstallError):
        await service.ensure("ladybug", "0.19.1")

    state = service.status("ladybug", "0.19.1")
    assert state.status == STATUS_FAILED
    assert "no wheel" in (state.error or "")


async def test_statuses_are_serialisable_for_the_form_to_poll(service, monkeypatch):
    stub_installs(monkeypatch)
    service.start_install("ladybug", "0.19.1")
    payload = [state.to_json() for state in service.statuses()]
    assert payload and set(payload[0]) >= {"engine", "version", "status", "detail"}


# -- opening a store --------------------------------------------------------


async def test_opening_a_store_uses_the_first_release_that_takes_it(
    service, monkeypatch, offline_map
):
    stub_installs(monkeypatch)
    pool = FakePool()
    monkeypatch.setattr(service_module, "WORKER_POOL", pool)

    store = fingerprint(storage_version=43)
    runtime, worker = await service.open_store(store)

    assert runtime.version == "0.19.1"
    assert pool.acquired == [("0.19.1", str(store.path), True)]


async def test_a_release_that_refuses_the_store_is_skipped_and_un_remembered(
    service, monkeypatch, offline_map
):
    stub_installs(monkeypatch)
    # 0.18.1 and 0.18.0 both claim to write format 42; pretend the newer one cannot
    # actually open this particular store.
    pool = FakePool(refuse={"0.18.1"})
    monkeypatch.setattr(service_module, "WORKER_POOL", pool)

    runtime, _worker = await service.open_store(fingerprint(storage_version=42))

    assert runtime.version == "0.18.0"
    assert [version for version, _path, _ro in pool.acquired][:2] == ["0.18.1", "0.18.0"]
    assert 42 not in service.version_map.records("ladybug")["0.18.1"].reads


async def test_a_successful_open_teaches_the_map_that_the_build_reads_that_format(
    service, monkeypatch, offline_map
):
    stub_installs(monkeypatch)
    monkeypatch.setattr(service_module, "WORKER_POOL", FakePool())

    await service.open_store(fingerprint(storage_version=42))

    # 0.18.1 was already known to *write* 42, so the fact learned here is the read,
    # and it is implied by the seed rather than news -- the file stays clean.
    assert 42 in service.version_map.records("ladybug")["0.18.1"].reads
    assert not offline_map.exists() or offline_map.read_text(encoding="utf-8").strip() in ("{}", "")


async def test_a_fall_forward_that_works_is_written_down(service, monkeypatch, offline_map):
    stub_installs(monkeypatch)
    # Nothing writes 44; 0.20.0 writes 47 and is the nearest above, so it is tried
    # and -- here -- succeeds. That is a genuinely new fact and must survive a restart.
    service.version_map.learn_writes("ladybug", "0.20.0", 47)
    monkeypatch.setattr(service_module, "WORKER_POOL", FakePool())

    runtime, _worker = await service.open_store(fingerprint(storage_version=44))

    assert runtime.version == "0.20.0"
    assert '"reads"' in offline_map.read_text(encoding="utf-8")
    assert 44 in service.version_map.records("ladybug")["0.20.0"].reads


async def test_a_format_nothing_can_read_names_what_the_proxy_does_know(
    service, monkeypatch, offline_map
):
    stub_installs(monkeypatch)
    monkeypatch.setattr(service_module, "WORKER_POOL", FakePool())

    # Kuzu never falls forward, so an unknown Kuzu format has no candidates at all.
    with pytest.raises(EngineResolutionError) as error:
        await service.open_store(fingerprint(engine="kuzu", storage_version=99))

    message = str(error.value)
    assert "storage version 99" in message
    assert "39" in message  # the formats it does know


async def test_a_store_no_candidate_can_open_reports_the_last_engine_error(
    service, monkeypatch, offline_map
):
    stub_installs(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "WORKER_POOL",
        FakePool(refuse={"0.19.1", "0.19.0", "0.20.0"}),
    )

    with pytest.raises(EngineResolutionError, match="not valid"):
        await service.open_store(fingerprint(storage_version=43))


async def test_a_writable_project_opens_a_different_worker(service, monkeypatch, offline_map):
    stub_installs(monkeypatch)
    pool = FakePool()
    monkeypatch.setattr(service_module, "WORKER_POOL", pool)

    await service.open_store(fingerprint(storage_version=43), read_only=False)

    assert pool.acquired[0][2] is False
