# -*- coding: utf-8 -*-
"""
The worker protocol, exercised over a real subprocess and a real pipe.

The engine is stubbed, not the transport. Every failure this pool has to survive --
a banner printed on stdout, an engine error, a process that dies mid-request -- is
a property of the pipe rather than of Kuzu, so a fake engine that misbehaves on
demand tests them better than a real one that behaves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kineviz_database_proxy.drivers.embedded import pool as pool_module
from kineviz_database_proxy.drivers.embedded.pool import (
    EngineWorker,
    EngineWorkerError,
    WorkerPool,
)
from kineviz_database_proxy.drivers.embedded.runtime import EngineRuntime
from kineviz_database_proxy.drivers.embedded.worker import (
    EngineSession,
    LatticeSession,
    _to_json_value,
    session_for,
)

STUB = '''
import json, os, sys

MODE = os.environ.get("STUB_MODE", "normal")
FRAMING = os.environ.get("STUB_FRAMING", "frame")

def send(message):
    """The worker's own framing, or a bare line when a test asks for one."""
    payload = json.dumps(message).encode("utf-8")
    out = sys.stdout.buffer
    if FRAMING == "frame":
        out.write(("GXRPROXY %d\\n" % len(payload)).encode("ascii"))
    out.write(payload)
    out.write(b"\\n")
    out.flush()

if MODE == "banner":
    print("kuzu: telemetry is enabled")
    print("not json at all")
    sys.stdout.flush()
if MODE == "longnoise":
    # A single line of noise past the reader's per-line limit, before any reply.
    print("N" * (2 * 1024 * 1024))
    sys.stdout.flush()

opened = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    op, rid = request.get("op"), request.get("id")
    if op == "shutdown":
        send({"ok": True, "id": rid}); break
    if op == "ping":
        reply = {"ok": True, "id": rid, "pong": True}
    elif op == "info":
        reply = {"ok": True, "id": rid, "version": "9.9.9", "storage_version": 99}
    elif op == "open":
        if MODE == "refuse":
            reply = {"ok": False, "id": rid, "error": "Unable to open database. Bad version."}
        else:
            opened["path"] = request["path"]
            reply = {"ok": True, "id": rid, "path": request["path"]}
    elif op == "query":
        if MODE == "die":
            sys.stderr.write("engine crashed horribly\\n"); sys.stderr.flush()
            os._exit(3)
        if MODE == "hang":
            continue  # a query the engine never answers
        if request["statement"] == "boom":
            reply = {"ok": False, "id": rid, "error": "Parser exception: nope"}
        elif request["statement"] == "huge":
            # Past the reader's per-line buffer, not merely past the 64 KiB default:
            # only reading the payload by byte count can carry this.
            rows = [[i, "y" * 250] for i in range(8000)]
            reply = {"ok": True, "id": rid,
                     "results": [{"columns": ["i", "pad"], "types": ["INT64", "STRING"],
                                  "rows": rows, "truncated": False}]}
        else:
            reply = {"ok": True, "id": rid,
                     "results": [{"columns": ["a"], "types": ["INT64"],
                                  "rows": [[1]], "truncated": False}]}
    else:
        reply = {"ok": False, "id": rid, "error": "unknown op"}
    send(reply)
'''


@pytest.fixture
def stub_runtime(tmp_path, monkeypatch):
    script = tmp_path / "stub_worker.py"
    script.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(pool_module, "WORKER_SCRIPT", script)
    return EngineRuntime(
        engine="kuzu", version="9.9.9", root=tmp_path, python=Path(sys.executable)
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "graph.kz"
    path.write_bytes(b"KUZU" + (39).to_bytes(8, "little"))
    return path


# -- value conversion -------------------------------------------------------


def test_bytes_cross_the_wire_as_base64():
    assert _to_json_value(b"hi") == "aGk="


def test_non_finite_floats_become_null():
    # json.dumps would emit NaN and Infinity, which are not JSON and which the
    # client's parser rejects outright.
    assert _to_json_value(float("nan")) is None
    assert _to_json_value(float("inf")) is None
    assert _to_json_value(1.5) == 1.5


def test_temporal_values_become_iso_strings():
    import datetime

    assert _to_json_value(datetime.date(2026, 8, 27)) == "2026-08-27"
    assert _to_json_value(datetime.datetime(2026, 8, 27, 10, 30)).startswith("2026-08-27T10:30")


def test_decimals_become_numbers_and_intervals_become_text():
    import datetime
    import decimal

    assert _to_json_value(decimal.Decimal("1.25")) == 1.25
    assert _to_json_value(datetime.timedelta(days=1)) == "1 day, 0:00:00"


def test_nested_containers_are_converted_all_the_way_down():
    value = {"list": [b"a", float("nan")], "set": {1}}
    converted = _to_json_value(value)
    assert converted["list"] == ["YQ==", None]
    assert converted["set"] == [1]


def test_a_kuzu_node_dict_survives_conversion_unchanged_in_shape():
    node = {"_id": {"offset": 0, "table": 0}, "_label": "Person", "name": "Alice"}
    assert _to_json_value(node) == node


# -- the protocol -----------------------------------------------------------


async def test_a_worker_opens_a_store_and_answers_a_query(stub_runtime, store):
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        response = await worker.request("query", statement="MATCH (n) RETURN n")
        assert response["results"][0]["rows"] == [[1]]
    finally:
        await worker.stop()


async def test_a_worker_can_start_without_a_store_to_ask_what_it_is(stub_runtime):
    # This is the discovery probe: install a release, ask its storage version, and
    # teach the version map -- with no database in hand yet.
    worker = EngineWorker(stub_runtime, None)
    try:
        await worker.start()
        assert (await worker.info())["storage_version"] == 99
    finally:
        await worker.stop()


async def test_noise_on_stdout_does_not_desynchronise_the_protocol(
    stub_runtime, store, monkeypatch
):
    monkeypatch.setenv("STUB_MODE", "banner")
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        assert (await worker.request("query", statement="x"))["results"][0]["columns"] == ["a"]
    finally:
        await worker.stop()


async def test_a_reply_far_past_the_readers_line_limit_arrives_whole(stub_runtime, store):
    # The bug this exists for: replies used to be read with readline(), which gives
    # up at 64 KiB with "Separator is not found, and chunk exceed the limit". Every
    # test payload was tiny, so it passed here and failed on the first real graph.
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        response = await worker.request("query", statement="huge")
        rows = response["results"][0]["rows"]
        assert len(rows) == 8000
        # Bigger than anything the reader will buffer for one line, so this can only
        # have arrived as a counted payload.
        assert len(json.dumps(response)) > pool_module.STREAM_LIMIT
    finally:
        await worker.stop()


async def test_the_stream_stays_in_step_after_a_large_reply(stub_runtime, store):
    # Reading the payload by byte count has to consume it exactly, trailing newline
    # included, or the next exchange reads the leftovers as garbage.
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        await worker.request("query", statement="huge")
        assert (await worker.request("query", statement="small"))["results"][0]["rows"] == [[1]]
        await worker.request("query", statement="huge")
        assert (await worker.info())["storage_version"] == 99
    finally:
        await worker.stop()


async def test_a_bare_json_reply_is_still_understood(stub_runtime, store, monkeypatch):
    # The frame header is what makes a large reply possible, but a small unframed
    # line is unambiguous and accepting it keeps the reader strictly more tolerant.
    monkeypatch.setenv("STUB_FRAMING", "bare")
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        assert (await worker.request("query", statement="small"))["results"][0]["rows"] == [[1]]
    finally:
        await worker.stop()


async def test_a_noise_line_past_the_limit_is_skipped_rather_than_fatal(
    stub_runtime, store, monkeypatch
):
    # An engine that dumps a megabyte-long line on stdout must not take the worker
    # with it: readline() raises there, and the reader has to carry on to the frame.
    monkeypatch.setenv("STUB_MODE", "longnoise")
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        assert (await worker.request("query", statement="small"))["results"][0]["rows"] == [[1]]
    finally:
        await worker.stop()


async def test_an_engine_error_is_raised_with_the_engines_own_message(stub_runtime, store):
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        with pytest.raises(EngineWorkerError, match="Parser exception"):
            await worker.request("query", statement="boom")
    finally:
        await worker.stop()


async def test_a_store_the_engine_refuses_fails_at_startup(stub_runtime, store, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "refuse")
    worker = EngineWorker(stub_runtime, str(store))
    with pytest.raises(EngineWorkerError, match="Bad version"):
        await worker.start()
    await worker.stop()


async def test_a_dead_process_reports_its_stderr_rather_than_hanging(
    stub_runtime, store, monkeypatch
):
    monkeypatch.setenv("STUB_MODE", "die")
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        with pytest.raises(EngineWorkerError) as error:
            await worker.request("query", statement="anything")
        assert "exited unexpectedly" in str(error.value)
        assert not worker.alive
    finally:
        await worker.stop()


async def test_a_request_that_outlives_its_timeout_stops_the_worker(
    stub_runtime, store, monkeypatch
):
    # An engine wedged on a query must not wedge the request with it: the wait is
    # bounded, and the worker is torn down so the next call gets a fresh one.
    monkeypatch.setenv("STUB_MODE", "hang")
    worker = EngineWorker(stub_runtime, str(store))
    try:
        await worker.start()
        with pytest.raises(EngineWorkerError, match="did not answer"):
            await worker.request("query", statement="x", timeout=0.3)
        assert not worker.alive
    finally:
        await worker.stop()


# -- the pool ---------------------------------------------------------------


async def test_the_same_store_reuses_one_worker(stub_runtime, store):
    pool = WorkerPool()
    try:
        first = await pool.acquire(stub_runtime, str(store))
        second = await pool.acquire(stub_runtime, str(store))
        assert first is second
    finally:
        await pool.shutdown()


async def test_read_only_and_writable_are_different_workers(stub_runtime, store):
    # They are different opens of the same file, and the engine holds a different
    # lock for each; sharing one process between them would be wrong.
    pool = WorkerPool()
    try:
        reader = await pool.acquire(stub_runtime, str(store), read_only=True)
        writer = await pool.acquire(stub_runtime, str(store), read_only=False)
        assert reader is not writer
    finally:
        await pool.shutdown()


async def test_a_dead_worker_is_replaced_rather_than_handed_out_again(
    stub_runtime, store, monkeypatch
):
    monkeypatch.setenv("STUB_MODE", "die")
    pool = WorkerPool()
    try:
        first = await pool.acquire(stub_runtime, str(store))
        with pytest.raises(EngineWorkerError):
            await first.request("query", statement="x")
        monkeypatch.setenv("STUB_MODE", "normal")
        second = await pool.acquire(stub_runtime, str(store))
        assert second is not first
        assert (await second.request("query", statement="x"))["results"]
    finally:
        await pool.shutdown()


async def test_an_idle_worker_is_swept(stub_runtime, store, monkeypatch):
    monkeypatch.setenv("GRAPHXR_PROXY_ENGINE_IDLE_SECONDS", "0.001")
    pool = WorkerPool()
    try:
        first = await pool.acquire(stub_runtime, str(store))
        first.last_used -= 5.0
        second = await pool.acquire(stub_runtime, str(store))
        assert second is not first
    finally:
        await pool.shutdown()


async def test_the_pool_does_not_grow_past_its_ceiling(stub_runtime, tmp_path):
    pool = WorkerPool(max_workers=2)
    try:
        for index in range(4):
            path = tmp_path / f"store{index}.kz"
            path.write_bytes(b"KUZU" + (39).to_bytes(8, "little"))
            await pool.acquire(stub_runtime, str(path))
        assert len(pool._workers) <= 2
    finally:
        await pool.shutdown()


async def test_releasing_a_worker_drops_it(stub_runtime, store):
    pool = WorkerPool()
    try:
        first = await pool.acquire(stub_runtime, str(store))
        await pool.release(stub_runtime, str(store))
        assert not first.alive
        assert await pool.acquire(stub_runtime, str(store)) is not first
    finally:
        await pool.shutdown()


# -- the LatticeDB session --------------------------------------------------
#
# LatticeDB is driven through a different object than the Kuzu family: the
# database is opened explicitly and answers with rows that are dicts, not
# sequences. These check the normalisation rather than the engine, so they use a
# stand-in module and stay offline.


class _FakeQueryResult:
    def __init__(self, columns, rows):
        self.columns = columns
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeLatticeDatabase:
    def __init__(self, path, **options):
        self.path = path
        self.options = options
        self.opened = False
        self.closed = False
        self.asked = []

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def query(self, statement, parameters=None):
        self.asked.append((statement, parameters))
        return _FakeQueryResult(
            ["id(n)", "labels(n)", "properties(n)"],
            [
                {"id(n)": 1, "labels(n)": ["Person"], "properties(n)": {"name": "Alice"}},
                {"id(n)": 2, "labels(n)": ["Person"], "properties(n)": {"name": "Bob"}},
            ],
        )


class _FakeLatticeModule:
    __version__ = "0.14.0"
    Database = _FakeLatticeDatabase


def _lattice_session():
    session = LatticeSession("latticedb")
    session.module = _FakeLatticeModule()
    return session


def test_latticedb_gets_its_own_session_and_the_others_do_not():
    assert isinstance(session_for("latticedb"), LatticeSession)
    assert isinstance(session_for("kuzu"), EngineSession)
    assert isinstance(session_for("ladybug"), EngineSession)


def test_opening_a_latticedb_store_opens_it_explicitly():
    """Constructing the object is not enough -- the Kuzu family's is, and this is not."""
    session = _lattice_session()

    assert session.open("/tmp/store.db", read_only=True) == {
        "path": "/tmp/store.db",
        "read_only": True,
    }
    assert session.database.opened is True
    assert session.database.options == {"read_only": True}


def test_a_latticedb_result_is_flattened_into_the_positional_shape():
    session = _lattice_session()
    session.open("/tmp/store.db")

    payload = session.query("MATCH (n) RETURN id(n), labels(n), properties(n)")

    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["columns"] == ["id(n)", "labels(n)", "properties(n)"]
    # Dict rows, projected back through the columns the statement asked for.
    assert result["rows"] == [
        [1, ["Person"], {"name": "Alice"}],
        [2, ["Person"], {"name": "Bob"}],
    ]
    # LatticeDB reports no column types at all, where Kuzu names one per column.
    assert result["types"] == []
    assert result["truncated"] is False


def test_a_latticedb_result_past_the_row_cap_is_marked_truncated():
    session = _lattice_session()
    session.open("/tmp/store.db")

    result = session.query("MATCH (n) RETURN id(n)", max_rows=1)["results"][0]

    # The stand-in always answers two rows; the cap is what stops at one.
    assert result["rows"] == [[1, ["Person"], {"name": "Alice"}]]
    assert result["truncated"] is True


def test_querying_a_latticedb_session_with_nothing_open_is_an_error():
    with pytest.raises(RuntimeError):
        _lattice_session().query("MATCH (n) RETURN id(n)")


def test_reopening_the_same_latticedb_store_does_not_reopen_it():
    session = _lattice_session()
    session.open("/tmp/store.db")
    first = session.database

    session.open("/tmp/store.db")

    assert session.database is first
    assert first.closed is False


def test_the_latticedb_storage_version_is_read_out_of_a_store_it_writes(tmp_path):
    """
    LatticeDB exposes no ``storage_version`` attribute, so the session finds the
    format by creating a scratch store and reading its own header back.
    """

    class WritingDatabase(_FakeLatticeDatabase):
        def open(self):
            super().open()
            # BDTL, then the format number as a uint16, written twice.
            with open(self.path, "wb") as handle:
                handle.write(b"BDTL" + (3).to_bytes(2, "little") + (3).to_bytes(2, "little"))

    module = _FakeLatticeModule()
    module.Database = WritingDatabase

    assert LatticeSession._storage_version(module) == 3


def test_a_storage_version_probe_that_writes_nothing_usable_answers_none():
    # Not an error: the caller falls back to the seeded map.
    assert LatticeSession._storage_version(_FakeLatticeModule()) is None
