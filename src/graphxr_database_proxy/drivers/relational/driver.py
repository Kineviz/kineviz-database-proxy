# -*- coding: utf-8 -*-
"""
``RelationalDriver`` — the contract surface of a project whose database is tables.

One class answers all of ``BaseDatabaseDriver`` for mssql, postgresql and mysql: the
three differ in SQL spelling, which ``engines/*.py`` owns, and in nothing else, so this
reads its engine through ``EngineSpelling`` rather than branching on a name.

The class is assembled here and its bodies are not. Each half is a mixin, and
``RelationalDriver`` puts all three **ahead of** ``RelationalReads`` so that a name which
used to be found on this class is still found before any base that also declares one:

* ``driver_mapping.py`` — resolving the project's ``EffectiveMapping``, override or draft.
* ``driver_query.py`` — running one statement, and shaping what the fan-out returns.
* ``driver_schema.py`` — the schema, sample, discovery and direction-preview reads.
* ``reads.py`` and ``schema_surface.py`` — the statement builders, and the contract documents.

What stays here is what has no other home: the engine and connection surface, the tuning
keys the other halves read, and ``get_api_info``, which opens no connection because a pool
is not somewhere to spend a page load. The Cypher-to-SQL stages are named only in
``sql_pipeline.py``, and nothing here imports a DBAPI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.graph_mapping import GraphMapping
from ...models.project import Project
from ..base import BaseDatabaseDriver
from . import query as query_layer
from .driver_mapping import RelationalMappingSurface
from .driver_query import RelationalQuerySurface
from .driver_schema import RelationalSchemaReadSurface
from .engine import engines_for
from .mapping import EffectiveMapping
from .pool import DbapiMissing, RelationalAccess
from .reads import RelationalReads
from .schema_surface import RelationalSchemaSurface
from .sql_pipeline import PIPELINE

# These names moved into a mixin module and are re-imported here so this module keeps the
# namespace it had; the code that calls them lives in the module each one moved to.
from .driver_mapping import _declared_key, _key_unnamed  # noqa: F401
from .driver_query import (  # noqa: F401
    _CALL_HEAD,
    _SCHEMA_PROBES,
    _combine,
    _failure,
    _problems_or,
    _shape_notes,
    _summary,
)
from .driver_schema import SAMPLE_ROWS  # noqa: F401


class RelationalDriver(
    RelationalMappingSurface,
    RelationalQuerySurface,
    RelationalSchemaReadSurface,
    RelationalReads,
    RelationalSchemaSurface,
    BaseDatabaseDriver,
):
    """
    The whole ``BaseDatabaseDriver`` surface for a relational project.

    A subclass sets two words — ``database_type`` and ``engine_name`` — and mixes in one
    engine's capability record and dialect. Everything else is here, including the mapping,
    which is what makes a table a label at all.
    """

    #: The URL segment, and the engine registry key when a driver names no engine.
    database_type = ""
    #: The ``EngineSpelling`` this driver compiles against.
    engine_name = ""

    def __init__(self, project: Project) -> None:
        super().__init__(project)
        self._access: Optional[RelationalAccess] = None
        self._mapping: Optional[EffectiveMapping] = None
        self._document: Optional[GraphMapping] = None
        self._spelling: Optional[Any] = None

    # -- engine and connection ---------------------------------------------

    @property
    def spelling(self) -> Any:
        """
        This project's engine spelling: the only place the three engines differ at all.

        Resolved once per driver rather than per call, because the query path reads it for
        every statement it compiles and the answer cannot change underneath one request.
        """
        if self._spelling is None:
            self._spelling = engines_for(
                self.engine_name or self.database_type or str(self.config.type)
            )
        return self._spelling

    @property
    def access(self) -> RelationalAccess:
        """
        The connection machinery for this project.

        Built per driver and holding no connection: the pool underneath is what outlives a
        request, which is why nothing else here may cache one.
        """
        if self._access is None:
            self._access = self.make_access()
        return self._access

    def make_access(self) -> RelationalAccess:
        """How this driver reaches a database. One line, so a test can hand a fake pool."""
        return RelationalAccess(self.project, self.spelling)

    async def connect(self) -> None:
        """
        Make sure this project's engine exists — which is all "connect" means here.

        A DBAPI that is not installed surfaces as ``ConnectionError`` carrying the engine's
        install sentence: that is a server-configuration fault, not something a query can
        answer, and no amount of retrying a statement installs ``pymssql``.
        """
        try:
            self.access.engine()
        except DbapiMissing as exc:
            raise ConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        """Let go of the engine without closing it."""
        self._access = None

    async def test_connection(self) -> bool:
        """One version probe: the cheapest statement that proves this is that server."""
        try:
            await self.access.probe()
        except Exception as exc:
            print("[{}] test_connection failed: {}".format(self.database_type, exc))
            return False
        return True

    # -- tuning and limits --------------------------------------------------

    def tuning(self, key: str, default: Any) -> Any:
        """
        One ``mapping.tuning`` override, then the project's ``options``, then the default.

        The document carries per-project query tuning because a person who knows their schema
        can say things discovery cannot — how many ids fit an ``IN`` here, how deep a fan-out
        may go. The config's ``options`` carry the same keys for a project with no mapping
        file yet, which is most of them on the first day.
        """
        document = self.override_document()
        table = (document.tuning if document is not None else None) or {}
        if key in table:
            return table[key]
        options = self.config.options or {}
        if key in options:
            return options[key]
        return default

    def default_limit(self) -> int:
        """The limit an absent ``LIMIT`` gets. Zero or less means the caller wants none."""
        return _positive_int(self.tuning("default_limit", None), query_layer.DEFAULT_LIMIT)

    def max_limit(self) -> Optional[int]:
        """The ceiling a hand-typed ``LIMIT`` cannot walk past, or None for no ceiling."""
        stated = self.tuning("max_limit", None)
        if stated is None:
            return None
        return _positive_int(stated, 0) or None

    def max_branches(self) -> int:
        """
        How many readings of one pattern the planner may enumerate: the query's total bound.

        The tuning key overrides ``plan.DEFAULT_MAX_BRANCHES``, which is the number the
        planner's refusal message quotes, so going through it is what keeps that message true.
        Refusing over the ceiling is right where trimming is not: a dropped branch is an answer
        that is quietly wrong. The chunks a branch splits into for a value list this server
        cannot carry are not capped — they are pieces of one branch's answer, and dropping one
        would drop the rows in it.
        """
        return _positive_int(self.tuning("max_branches", None), PIPELINE.default_max_branches())

    def refusals(self) -> query_layer.Refusals:
        """
        The engine's own additions to the shared refusal list.

        T-SQL has no regular-expression operator at all, so ``=~`` there has to be refused by
        name rather than answered with an empty canvas — which is the whole reason
        ``Refusals`` is a caller-supplied list instead of a rule inside ``query.py``.
        """
        if self.spelling.supports_regex:
            return query_layer.NO_REFUSALS
        return query_layer.Refusals.of(query_layer.REGEX_REFUSAL)

    # -- api ----------------------------------------------------------------

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        """
        The route table for this project.

        Synchronous and connection-free on purpose: opening a pool to tell someone what their
        URLs are would make an unreachable database a slow page rather than an honest error
        on one.
        """
        base = "/api/{}/{}".format(self.database_type or str(self.config.type), project_name)
        return {
            "type": self.database_type,
            "api_urls": {
                "info": base,
                "query": "{}/query".format(base),
                "schema": "{}/schema".format(base),
                "graphSchema": "{}/graphSchema".format(base),
                "sampleData": "{}/sampleData".format(base),
                "capabilities": "{}/capabilities".format(base),
                "expand": "{}/expand".format(base),
                "pullCategory": "{}/pullCategory".format(base),
                "pullRelationship": "{}/pullRelationship".format(base),
                "test": "{}/test".format(base),
                "mapping": "/api/relational/{}".format(project_name),
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "cypher": True,
                "sql": True,
                "graph_schema": True,
                "table_schema": True,
                "sample_data": True,
                "relational": True,
                "read_only": True,
                "fulltext_search": False,
                "multi_database": False,
                "engine": self.spelling.name,
                "schema": self.access.schema,
                "mapping": bool(self.config.graph_mapping),
            },
        }


def _positive_int(value: Any, default: int) -> int:
    try:
        found = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return found if found > 0 else default
