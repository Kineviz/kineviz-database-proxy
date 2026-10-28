# -*- coding: utf-8 -*-
"""
Engines, connections, and the bounded reads that go through them.

The API builds a fresh driver for every request — ``_ensure_graph_categories`` has to
be able to re-probe, and a driver that cached a connection would hold one per canvas
turn — so anything that outlives a request cannot live on a driver. What outlives one
here is a connection pool, and this module owns it.

**One SQLAlchemy engine per project config, cached process-wide, keyed by
``(project.id, sha256(database_config))``.** The digest is of the serialised config, so
editing a project's host, its schema *or its graph mapping* is a different key: the old
engine is disposed on the spot rather than kept warm for a database the person has just
walked away from. It is a digest and never a copy — the config holds the password, and
the pool is not somewhere to leave one.

``sqlalchemy.create_engine`` is only ever reached through here, and only ever after the
DBAPI has been checked for. No DBAPI is installed in this venv (they are optional
extras), so a real connect is not something the test suite can do; everything below
takes a spelling and an inspector as they are, and ``tests/test_relational_pool.py``
drives it with a fake engine. That is also why a missing driver package arrives as
``DbapiMissing`` carrying the engine's own install sentence, and never as an
``ImportError`` traceback from inside SQLAlchemy's dialect loader.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.engine import Connection, Engine

from ...models.project import Project
from .catalog import Catalog, Inspector, INTERNAL_SCHEMAS, _visible_names, discover
from .engine import EngineSpelling, ServerInfo, configured_schema

#: Names one reflection round trip may be asked about. Above this, ``/tables`` has
#: asked for a page bigger than a page and the smaller answer is the honest one.
MAX_NAME_PAGE = 500

#: The default page for ``/tables`` — the picker's, not a database dump's.
DEFAULT_NAME_PAGE = 50


class DbapiMissing(RuntimeError):
    """
    The engine's DBAPI is not installed.

    ``str`` of it is what the person sees: the spelling's own install sentence, which
    names the extra *and* the package. A traceback out of ``create_engine`` would name
    neither and read like a bug in the proxy.
    """


def config_digest(config: Any) -> str:
    """
    A stable fingerprint of one project's connection config.

    Serialised the way the cache does it, so "the graph mapping changed" and "the host
    changed" both show up here: they are both a different database to be talking to.
    """
    try:
        dumped = config.model_dump(mode="json")
    except AttributeError:  # a duck-typed config in a test, or an old serialised one
        dumped = dict(getattr(config, "__dict__", {}) or {})
    payload = json.dumps(dumped, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class EnginePool:
    """The engine cache itself: one engine per project config, disposed on a change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engines: Dict[Tuple[str, str], Engine] = {}
        self._digest_by_project: Dict[str, str] = {}

    def engine(self, project: Project, spelling: EngineSpelling) -> Engine:
        """
        The pooled engine for this project's *current* config.

        A config edit disposes the engine it replaced before the new one is built, so a
        project never holds two pools open at once, and never keeps a pool to a server
        it no longer names.
        """
        digest = config_digest(project.database_config)
        with self._lock:
            previous = self._digest_by_project.get(project.id)
            if previous is not None and previous != digest:
                self._dispose_locked(previous_project=self._project_key(project.id))
            found = self._engines.get((project.id, digest))
            if found is not None:
                self._digest_by_project[project.id] = digest
                return found
            made = self._build(project, spelling)
            self._engines[(project.id, digest)] = made
            self._digest_by_project[project.id] = digest
            return made

    def dispose_project(self, project_id: str) -> None:
        """Drop every engine for one project, whatever config each was built under."""
        with self._lock:
            self._dispose_locked(previous_project=project_id)

    def dispose_all(self) -> None:
        """Drop everything. A shutdown handler should call this, so pools close clean."""
        with self._lock:
            for key in list(self._engines):
                self._pop_locked(key)

    # -- internals ---------------------------------------------------------

    def _project_key(self, project_id: str) -> str:
        return project_id

    def _build(self, project: Project, spelling: EngineSpelling) -> Engine:
        config = project.database_config
        _require_dbapi(spelling)
        url = spelling.url(config)
        try:
            return create_engine(url, connect_args=spelling.connect_args(config))
        except DbapiMissing:
            raise
        except Exception as exc:  # a dialect that cannot load counts as a missing driver
            raise DbapiMissing(
                "{} could not be connected: {}. {}".format(
                    spelling.name, exc, spelling.missing_dbapi_message()
                )
            ) from exc

    def _dispose_locked(self, previous_project: str) -> None:
        for key in [key for key in self._engines if key[0] == previous_project]:
            self._pop_locked(key)
        self._digest_by_project.pop(previous_project, None)

    def _pop_locked(self, key: Tuple[str, str]) -> None:
        engine = self._engines.pop(key, None)
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                # A pool that cannot close is not worth raising over: the caller is
                # either re-pointing the project or shutting the process down, and
                # neither is served by losing the request to a cleanup failure.
                pass


#: The process-wide pool.
POOL = EnginePool()


def _require_dbapi(spelling: EngineSpelling) -> None:
    """
    Check the driver package is importable *before* SQLAlchemy is asked to load it.

    ``create_engine`` answers an absent DBAPI with a ``ModuleNotFoundError`` wrapped in
    a ``NoSuchModuleError`` whose text names ``sqlalchemy.dialects.mssql.pymssql`` —
    true, unhelpful, and easy to mistake for a broken proxy. This answers with the
    engine's own install sentence instead.
    """
    module = str(getattr(spelling, "dbapi", "") or "").strip()
    if not module:
        return
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        found = False
    if not found:
        raise DbapiMissing(spelling.missing_dbapi_message())


def inspector_for(engine: Engine) -> Inspector:
    """The catalog reader for an engine. A seam: tests hand back a fake inspector."""
    return sa_inspect(engine)  # type: ignore[return-value]


class RelationalAccess:
    """
    Everything that needs a live connection, for one project.

    Every method is async and runs its sync work on a worker thread: SQLAlchemy is
    synchronous, and a proxy that served a browser canvas cannot spend the event loop
    waiting on someone's warehouse.
    """

    def __init__(
        self,
        project: Project,
        spelling: EngineSpelling,
        pool: EnginePool = POOL,
        inspector: Callable[[Engine], Inspector] = inspector_for,
    ) -> None:
        self.project = project
        self.spelling = spelling
        self._pool = pool
        self._inspector_for = inspector
        self._server: Optional[ServerInfo] = None

    # -- the basics --------------------------------------------------------

    @property
    def schema(self) -> Optional[str]:
        """The schema names these tables: the field, the option, the engine default."""
        return configured_schema(self.project.database_config, self.spelling.default_schema)

    def engine(self) -> Engine:
        return self._pool.engine(self.project, self.spelling)

    def inspector(self) -> Inspector:
        engine = self.engine()
        if self._inspector_for is not inspector_for:
            return self._inspector_for(engine)
        custom = self.spelling.catalog_inspector(engine, self.project.database_config)
        return custom if custom is not None else self._inspector_for(engine)

    async def names_page(self, *, schema: Optional[str], offset: int, limit: int,
                         search: Optional[str] = None) -> Optional[Tuple[List[str], int]]:
        """Use source pagination when supplied by the catalog adapter."""
        if not self.spelling.supports_catalog_paging:
            return None
        want = self.schema if schema is None else schema

        def work(conn: Connection) -> Optional[Tuple[List[str], int]]:
            reader = getattr(self.inspector(), "get_table_names_page", None)
            return reader(want, offset, limit, search) if reader else None

        return await self.run(work)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """
        One checked-out connection, set up the way this engine wants it.

        Session statements first, then the isolation level the person asked for, then
        the read-only transaction where the engine has one. All three are the engine's
        answers, never a rule written here.
        """
        config = self.project.database_config
        isolation = self.spelling.isolation(config)
        with self.engine().connect() as raw:
            conn = raw.execution_options(isolation_level=isolation) if isolation else raw
            for statement in self.spelling.session_statements():
                conn.execute(text(statement))
            read_only = self.spelling.read_only_statement()
            if read_only:
                # The DBAPI-level ``BEGIN`` SQLAlchemy has just issued is empty, so the
                # implicit commit the server performs to honour this one gives back
                # nothing it held.
                conn.execute(text(read_only))
            yield conn

    # -- reads -------------------------------------------------------------

    async def probe(self) -> ServerInfo:
        """What the server says it is, once per access: the answer cannot change mid-request."""
        if self._server is None:
            self._server = await self.run(lambda conn: self.spelling.probe_server(conn))
        return self._server

    async def run(self, work: Callable[[Connection], Any]) -> Any:
        """One unit of synchronous work on its own connection, off the event loop."""
        return await asyncio.to_thread(self._run_sync, work)

    def _run_sync(self, work: Callable[[Connection], Any]) -> Any:
        with self.connection() as conn:
            return work(conn)

    async def rows(
        self,
        statement: Any,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """One SELECT, as plain dicts, on a connection of its own."""

        def work(conn: Connection) -> List[Dict[str, Any]]:
            result = conn.execute(statement, dict(parameters or {}))
            return [dict(row._mapping) for row in result]

        return await self.run(work)

    async def names(self, schema: Optional[str] = None) -> List[str]:
        """
        The visible table names, with nothing reflected.

        ``/tables`` pages on names, so it has to know them before it can ask for one page
        of detail — and the filter that decides what is visible (a view, a system table, an
        engine's own schema) is ``catalog``'s, not a second rule written here. Listing names
        is one catalog query a DB-API inspector cannot offset; everything that costs more
        than a name stays bounded by the page that asked for it.
        """
        want = self.schema if schema is None else schema

        def work(conn: Connection) -> List[str]:
            return _visible_names(self.inspector(), want, Catalog(schema=want), INTERNAL_SCHEMAS)

        return await self.run(work)

    async def catalog(
        self,
        schema: Optional[str] = None,
        names: Optional[Sequence[str]] = None,
        page_size: Optional[int] = None,
    ) -> Catalog:
        """
        The schema read as tables.

        Pass ``names`` to read *one page* of them instead of the whole schema: the walk
        is per page of names already, and an endpoint that shows twenty tables must not
        pay for three thousand.
        """
        want_schema = self.schema if schema is None else schema
        size = max(1, min(int(page_size or MAX_NAME_PAGE), MAX_NAME_PAGE))

        def work(conn: Connection) -> Catalog:
            # The estimate reader is bound to *this* connection, because a page of
            # names must not open a page of connections.
            counted = self._count_reader(conn, want_schema)
            inspector = self.inspector()
            source = inspector if names is None else _NamePage(inspector, names)
            catalog = discover(source, schema=want_schema, row_counts=counted, page_size=size)
            catalog.warnings.extend(self.spelling.discovery_warnings)
            return catalog

        return await self.run(work)

    async def counts(
        self, tables: Sequence[str], schema: Optional[str] = None
    ) -> Dict[str, int]:
        """Estimated rows per table, from statistics only, for the names asked about."""
        want_schema = self.schema if schema is None else schema
        names = [str(name) for name in tables]

        def work(conn: Connection) -> Dict[str, int]:
            return dict(self.spelling.approx_row_counts(conn, want_schema, names))

        return await self.run(work)

    def _count_reader(
        self, conn: Connection, schema: Optional[str]
    ) -> Callable[[Sequence[str]], Mapping[str, int]]:
        """
        Row estimates for one page of names, bound to one connection.

        Statistics only, never a scan; a table the statistics have nothing for stays
        absent rather than becoming a confident zero.
        """
        spelling = self.spelling
        return lambda names: spelling.approx_row_counts(conn, schema, names)

    # -- helpers used by the driver ---------------------------------------

    def timeout_hint(self) -> str:
        """The engine's own ``SELECT`` hint, empty where the guard is a session setting."""
        return self.spelling.timeout_hint(self.project.database_config)


class _NamePage:
    """
    An inspector narrowed to one page of table names.

    ``discover`` reads names once and then reflects a page of names at a time, so the
    page of names *is* the unit of work: handing it an inspector that only knows this
    page is how ``/tables?page_size=20`` costs twenty tables of reflection instead of
    three thousand. Every ``get_multi_*`` forwards unchanged, because
    ``reflect._read_pages`` has already sliced the names it was given down to one page.

    Listing names itself stays one catalog query — a DB-API inspector cannot offset it —
    and that is the only whole-schema read left in the walk.
    """

    def __init__(self, inspector: Inspector, names: Sequence[str]) -> None:
        self._inspector = inspector
        self._names = [str(name) for name in names]

    def get_table_names(self, schema: Optional[str] = None) -> List[str]:
        return list(self._names)

    def __getattr__(self, item: str) -> Any:
        """Everything else — views, indexes, the multi-reads — goes to the real one."""
        return getattr(self._inspector, item)
