"""
The engine pool and the bounded reads, with no DBAPI installed and none needed.

This file also holds the fakes every other relational test reads through, because the
thing they all have to prove is the same: a page of names is the unit of work, a config
edit must not leave a pool open against the database it no longer names, and nothing here
may require ``pymssql`` to be importable.

The spelling these tests drive is SQL Server's real one with three answers replaced — the
connect URL, the version probe and the row-count statistics — so the type table, the
identifier quoting and the refusal wiring a driver sees are the production ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
from sqlalchemy import create_engine

from graphxr_database_proxy.drivers.relational import pool as pool_module
from graphxr_database_proxy.drivers.relational.compile import COLUMN_PREFIX
from graphxr_database_proxy.drivers.relational.engine import ServerInfo, engines_for
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.pool import (
    DEFAULT_NAME_PAGE,
    DbapiMissing,
    EnginePool,
    RelationalAccess,
    config_digest,
)
from graphxr_database_proxy.drivers.relational.catalog import propose
from graphxr_database_proxy.models.project import AuthType, DatabaseConfig, DatabaseType, Project
from tests.test_relational_reflect import SPEC, FakeInspector

# ---------------------------------------------------------------------------
# the fakes the relational tests share
# ---------------------------------------------------------------------------

SERVER = ServerInfo(
    engine="mssql", version="15.0.2000.5", major=15, minor=0, build=2000,
    compat_level=150, release_name="2019",
)


class FakeSpelling(MSSQLEngine):
    """
    SQL Server's answers, with the transport and the two server reads replaced.

    The type table, ``column_type``, quoting and ``name_case_rule`` stay real: a driver
    test that faked those would pass while the engine it is wired to changed underneath it.
    """

    dbapi = "sqlite3"

    def __init__(self, backend: str = "mssql", supports_regex: Optional[bool] = None) -> None:
        self.name = backend
        if supports_regex is not None:
            self.supports_regex = supports_regex
        self.probe_calls = 0
        self.count_pages: List[Sequence[str]] = []
        self.counts: Dict[str, int] = {}

    def url(self, config: Any, *, password: Optional[str] = None) -> str:
        return "sqlite://"

    def connect_args(self, config: Any) -> Dict[str, object]:
        return {}

    def probe_server(self, connection: Any) -> ServerInfo:
        self.probe_calls += 1
        return SERVER

    def approx_row_counts(
        self, connection: Any, schema: Optional[str], tables: Sequence[str]
    ) -> Dict[str, int]:
        names = list(tables)
        self.count_pages.append(names)
        return {name: self.counts[name] for name in names if name in self.counts}


class FakeRow:
    """A row that answers both ways: ``row["col"]`` for the mapping, ``row[0]`` for the probes."""

    def __init__(self, values: Any) -> None:
        self._mapping = values if isinstance(values, dict) else dict(enumerate(values))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]


class FakeResult:
    def __init__(self, rows: Sequence[Any]) -> None:
        self._rows = list(rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeConnection:
    """Records every statement it is handed and answers from the engine's script."""

    def __init__(self, engine: "FakeEngine") -> None:
        self.engine = engine
        self.isolation: Optional[str] = None

    def execution_options(self, isolation_level: Optional[str] = None) -> "FakeConnection":
        self.isolation = isolation_level
        return self

    def execute(self, statement: Any, parameters: Optional[Dict[str, Any]] = None) -> FakeResult:
        self.engine.executed.append((str(statement), dict(parameters or {})))
        return FakeResult(self.engine.rows_for(statement))

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeEngine:
    """
    A pooled engine that is not connected to anything.

    ``script`` is the queue of row-sets returned, one per SELECT, in order: a test says how
    many statements it expects by how many row-sets it supplies, which is also what makes an
    unexpected extra statement fail loudly instead of returning nothing. ``row_factory``
    instead builds each row from the columns the statement actually projected, which is what
    lets a driver test assert a *graph* without hard-coding the compiler's column labels.
    """

    def __init__(
        self,
        script: Optional[Sequence[Sequence[Any]]] = None,
        row_factory: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        self.script: List[Sequence[Any]] = list(script or [])
        self.row_factory = row_factory
        self.error = error
        self.executed: List[Tuple[str, Dict[str, Any]]] = []
        self.selects: List[Any] = []
        self.opens = 0
        self.disposed = 0

    def connect(self) -> FakeConnection:
        self.opens += 1
        return FakeConnection(self)

    def dispose(self) -> None:
        self.disposed += 1

    def rows_for(self, statement: Any) -> List[Any]:
        sql = str(statement)
        if not sql.lstrip().upper().startswith("SELECT"):
            return []
        if self.error:
            raise RuntimeError(self.error)
        self.selects.append(statement)
        if self.row_factory is not None:
            return [FakeRow(row) for row in self.row_factory(statement)]
        return [FakeRow(row) for row in (self.script.pop(0) if self.script else [])]


#: ``compile.py``'s projection labels: ``__gx_v<vertex>_k<index>``, ``__gx_v<vertex>_p<n>``
#: and the same for an edge, so a row can be filled without hard-coding one.
_GX = COLUMN_PREFIX + "_"


def _gx_slot(name: str) -> Optional[Tuple[str, str]]:
    """``__gx_v0_k0`` → ``('v', 'k0')``; a column the driver projects itself is ``None``."""
    if not name.startswith(_GX):
        return None
    parts = name[len(_GX) :].split("_")
    if len(parts) != 2 or not parts[0][1:].isdigit():
        return None
    return parts[0][0], parts[1]


def graph_rows(keys: Sequence[Any], edge_keys: Sequence[Any] = (7,), values: str = "value") -> Any:
    """
    One row per statement, filled from the columns that statement asked for.

    A stand-in for a database answering a SELECT: it knows nothing about the mapping, only
    that a vertex's key column carries the given key and every other column carries
    something. That is the property a driver test needs — an id minted from a real key value
    — without pinning the compiler's label scheme. A column that is not one of the compiler's
    (a sample projecting ``full_name``) is answered by name, because the driver's own reads
    project the mapping's columns and a test on them wants the columns back.
    """

    def build(statement: Any) -> List[Dict[str, Any]]:
        row: Dict[str, Any] = {}
        for column in statement.selected_columns:
            name = str(getattr(column, "name", "") or "")
            slot = _gx_slot(name)
            if slot is None:
                row[name] = "{}-{}".format(name, values)
                continue
            kind, field = slot
            if field[0] != "k":
                row[name] = "{}-{}".format(name, values)
                continue
            source = list(keys if kind == "v" else edge_keys)
            index = int(field[1:])
            row[name] = source[index] if index < len(source) else source[-1]
        return [row]

    return build


class FakePool:
    """The one method ``RelationalAccess`` calls on a pool, with a count of the asks."""

    def __init__(self, engine: FakeEngine) -> None:
        self.engine_object = engine
        self.calls = 0
        self.disposed: List[str] = []

    def engine(self, project: Project, spelling: Any) -> FakeEngine:
        self.calls += 1
        return self.engine_object

    def dispose_project(self, project_id: str) -> None:
        self.disposed.append(project_id)


def draft_document() -> Any:
    """Discovery's proposal for the shared spec, so a test can save it as an override."""
    return propose(FakeInspector(SPEC))


def config(**overrides: Any) -> DatabaseConfig:
    base: Dict[str, Any] = dict(
        type=DatabaseType.MSSQL,
        host="db.internal",
        port=1433,
        username="graphxr",
        password="s3cret",
        auth_type=AuthType.USERNAME_PASSWORD,
    )
    base.update(overrides)
    return DatabaseConfig(**base)


def project(name: str = "relational-test", **config_overrides: Any) -> Project:
    """A project built in memory: config/projects.json is live state and is never opened."""
    made = Project(
        id=name, name=name, database_type=DatabaseType.MSSQL, database_config=config(**config_overrides)
    )
    return made


def mapped_project(name: str = "relational-mapped", **overrides: Any) -> Project:
    """A project whose config carries the discovered draft as its override."""
    document = draft_document().model_dump(by_alias=True)
    document.update(overrides.pop("mapping", None) or {})
    return project(name, graph_mapping=document, **overrides)


def access_for(
    target: Project, spelling: Optional[FakeSpelling] = None, engine: Optional[FakeEngine] = None,
    inspector: Optional[Any] = None,
) -> RelationalAccess:
    """An access over a fake pool and a fake inspector — the whole DB layer of a driver."""
    return RelationalAccess(
        target,
        spelling or FakeSpelling(),
        pool=FakePool(engine or FakeEngine()),
        inspector=lambda _engine: inspector or FakeInspector(SPEC),
    )


@dataclass
class Wired:
    """A driver with its engine, spelling and access beside it, so a test can read what ran."""

    driver: Any
    engine: FakeEngine
    spelling: FakeSpelling
    access: RelationalAccess


def driver_for(
    target: Project,
    script: Optional[Sequence[Sequence[Any]]] = None,
    row_factory: Optional[Any] = None,
    backend: str = "mssql",
    supports_regex: Optional[bool] = None,
    inspector: Optional[Any] = None,
) -> Wired:
    """
    One of the three real drivers, wired to the fakes above.

    The driver class, its capability record and its dialect are production; only the
    transport is not. That is what lets these tests assert what GraphXR would receive from a
    ``/expand`` rather than what a stand-in would have answered.
    """
    from graphxr_database_proxy.drivers.factory import DriverFactory

    made = DriverFactory.create_driver(target)
    spelling = FakeSpelling(backend, supports_regex=supports_regex)
    engine = FakeEngine(script, row_factory)
    made._spelling = spelling
    made._access = access_for(target, spelling=spelling, engine=engine, inspector=inspector)
    return Wired(driver=made, engine=engine, spelling=spelling, access=made._access)


# ---------------------------------------------------------------------------
# the key: a config change is a different engine
# ---------------------------------------------------------------------------


def test_the_digest_is_a_digest_and_not_a_copy() -> None:
    """
    The pool keys on a hash of the config, so it never holds a password.

    A graph mapping is part of the config, which is the whole reason saving one invalidates
    the cached schema by itself — so it has to change the digest too.
    """
    plain = config()
    moved = config(graph_mapping={"version": 1, "nodes": [], "edges": []})

    assert config_digest(plain) != config_digest(moved)
    assert config_digest(plain) == config_digest(config())
    assert "s3cret" not in config_digest(plain)
    assert len(config_digest(plain)) <= 64


def test_one_config_one_engine_and_a_second_call_is_free() -> None:
    pool = EnginePool()
    built: List[Any] = []
    original = pool_module.create_engine

    def counted(url: Any, **kwargs: Any) -> Any:
        made = original(url, **kwargs)
        built.append(made)
        return made

    pool_module.create_engine = counted  # type: ignore[assignment]
    try:
        target = project("reuse")
        first = pool.engine(target, FakeSpelling())
        second = pool.engine(project("reuse"), FakeSpelling())
    finally:
        pool_module.create_engine = original  # type: ignore[assignment]

    assert first is second
    assert len(built) == 1


def test_editing_the_config_disposes_the_engine_it_replaced() -> None:
    pool = EnginePool()
    engines: List[Any] = []
    original = pool_module.create_engine

    def counted(url: Any, **kwargs: Any) -> Any:
        made = original(url, **kwargs)
        engines.append(made)
        return made

    pool_module.create_engine = counted  # type: ignore[assignment]
    try:
        pool.engine(project("edited"), FakeSpelling())
        pool.engine(project("edited", host="other.internal"), FakeSpelling())
        keys = list(pool._engines)
    finally:
        pool_module.create_engine = original  # type: ignore[assignment]

    assert len(engines) == 2
    assert len(keys) == 1, "the pool kept an engine to a server the config no longer names"
    assert keys[0] == ("edited", config_digest(project("edited", host="other.internal").database_config))


def test_disposing_a_project_or_the_process_closes_every_engine() -> None:
    pool = EnginePool()
    original = pool_module.create_engine
    engines: List[Any] = []

    def counted(url: Any, **kwargs: Any) -> Any:
        made = original(url, **kwargs)
        engines.append(made)
        return made

    pool_module.create_engine = counted  # type: ignore[assignment]
    try:
        pool.engine(project("a"), FakeSpelling())
        pool.engine(project("b"), FakeSpelling())
        assert len(pool._engines) == 2
        pool.dispose_project("a")
        assert list(pool._engines) and next(iter(pool._engines))[0] == "b"
        pool.dispose_all()
        assert not pool._engines
    finally:
        pool_module.create_engine = original  # type: ignore[assignment]

    assert len(engines) == 2


def test_a_missing_dbapi_says_what_to_install() -> None:
    """
    ``pymssql`` is not installed here, and that is the normal state of this venv.

    The check runs before SQLAlchemy is asked to load a dialect, so what a person reads is
    the engine's install sentence and not ``No module named 'sqlalchemy.dialects.mssql'``.
    """
    real = engines_for("mssql")
    with pytest.raises(DbapiMissing) as caught:
        EnginePool().engine(project("needs-driver"), real)

    message = str(caught.value)
    assert "pip install" in message and "pymssql" in message
    assert "sqlalchemy.dialects" not in message


# ---------------------------------------------------------------------------
# the access: one connection per unit of work, and one page per read
# ---------------------------------------------------------------------------


async def test_the_connection_is_set_up_the_way_the_engine_asks() -> None:
    target = project("setup")
    engine = FakeEngine()
    found = access_for(target, engine=engine)

    with found.connection() as conn:
        pass

    assert conn.isolation  # the isolation level went through execution_options
    asked = [sql for sql, _ in engine.executed]
    assert any("READ ONLY" in sql.upper() or "ARITHABORT" in sql.upper() for sql in asked)
    assert engine.opens == 1


async def test_a_page_of_names_is_one_page_of_reflection() -> None:
    """``/tables`` for twenty tables must not open the library, so the inspector is asked once."""
    big: Dict[str, Any] = {"Person": SPEC["Person"]}
    for index in range(10_000):
        big["T{:06d}".format(index)] = {
            "columns": [("id", "int", False), ("note", "varchar(10)", True)],
            "pk": (None, ["id"]),
            "fks": [],
        }
    inspector = FakeInspector(big)
    target = project("library")
    found = access_for(target, inspector=inspector)
    names = ["T{:06d}".format(index) for index in range(20)]

    read = await found.catalog(names=names, page_size=len(names))

    assert len(read.tables) == 20
    columns_ask = [ask for ask in inspector.calls if ask[0] == "columns"]
    assert len(columns_ask) == 1
    assert set(columns_ask[0][1]) == set(names)


async def test_row_estimates_come_from_statistics_and_default_to_absent() -> None:
    spelling = FakeSpelling()
    found = access_for(project("counts"), spelling=spelling)

    read = await found.catalog(names=["Person", "Order"], page_size=2)

    assert [table.row_estimate for table in read.tables] == [None, None]
    assert spelling.count_pages == [["Person", "Order"]]


def test_name_pages_are_bounded_and_the_default_is_smaller_than_the_ceiling() -> None:
    assert DEFAULT_NAME_PAGE < pool_module.MAX_NAME_PAGE
