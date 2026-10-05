"""
Wires the shared intent machinery onto the concrete drivers.

A driver gets intent support by declaring two things — a capability record and a
dialect — and by being able to answer "what are this graph's categories and their
key types". Everything else comes from ``GraphIntentMixin``.

Kept in its own module so ``spanner.py`` and ``rocketgraph.py`` each gain a mixin
and a handful of properties rather than a copy of the traversal logic.
"""

from __future__ import annotations

from typing import Any, Dict

from ..contract.capabilities import GraphDbCapabilities, assert_capabilities_consistent
from .dialect import (
    BIGQUERY_DIALECT,
    KUZU_DIALECT,
    LADYBUG_DIALECT,
    LATTICEDB_DIALECT,
    MEMGRAPH_DIALECT,
    MSSQL_DIALECT,
    MYSQL_DIALECT,
    NEO4J_DIALECT,
    POSTGRESQL_DIALECT,
    ROCKETGRAPH_DIALECT,
    SPANNER_DIALECT,
    StatementDialect,
)
from .intents import GraphIntentMixin


def _spanner_capabilities() -> GraphDbCapabilities:
    """
    Spanner Graph: GQL with `ELEMENT_ID()`, so seeds are re-selected by identity.
    Read-only through the proxy, and no full-text index to search.
    """
    capabilities = GraphDbCapabilities(
        type="spanner",
        queryLanguages=["gql", "sql"],
        defaultQueryLanguage="gql",
    )
    capabilities.identity.nodeId = "internal-id"
    capabilities.expand.predicate = "internal-id"
    capabilities.expand.directions = ["all", "from", "to", "both"]
    capabilities.expand.multiHop = True
    capabilities.expand.excludeRelationshipIds = True
    # GQL cannot restrict a traversal to a fixed node set on both ends.
    capabilities.expand.internalRelationships = False
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)
    return capabilities


def _rocketgraph_capabilities() -> GraphDbCapabilities:
    """
    RocketGraph / XGT: no node-identity function, so node ids are `<Label>:<key>`
    and seeds are re-selected by their key property. Only the two directed forms
    can be expressed without a way to de-duplicate an undirected traversal.
    """
    capabilities = GraphDbCapabilities(type="rocketgraph", queryLanguages=["cypher"])
    capabilities.identity.nodeId = "label-key"
    capabilities.expand.predicate = "primary-key"
    capabilities.expand.directions = ["from", "to"]
    capabilities.expand.multiHop = False
    capabilities.expand.excludeRelationshipIds = False
    capabilities.expand.internalRelationships = False
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)
    return capabilities


def _bigquery_capabilities() -> GraphDbCapabilities:
    """
    BigQuery graph: the same GQL as Spanner, with one difference that matters
    here — there is no ``ELEMENT_ID()``, so identity is read out of the JSON form
    of the element instead. Everything else is Spanner's record verbatim, because
    that kinship is real rather than incidental.
    """
    capabilities = GraphDbCapabilities(**SPANNER_CAPABILITIES.model_dump(by_alias=True))
    capabilities.type = "bigquery"
    assert_capabilities_consistent(capabilities)
    return capabilities


def _bolt_family_capabilities(name: str) -> GraphDbCapabilities:
    """
    Neo4j's record, and the baseline every bolt-compatible backend starts from.

    ``ID()`` gives both a node-identity and an edge-identity function, so seeds are
    re-selected by identity, hidden relationship types can be filtered in a
    predicate, and a traversal can be pinned to a fixed node set on both ends —
    the widest expand surface any of the proxied backends offers.
    """
    capabilities = GraphDbCapabilities(type=name, queryLanguages=["cypher"])
    capabilities.identity.nodeId = "internal-id"
    capabilities.expand.predicate = "internal-id"
    capabilities.expand.directions = ["all", "from", "to", "both"]
    capabilities.expand.multiHop = True
    capabilities.expand.relationshipTypeFilter = True
    capabilities.expand.internalRelationships = True
    capabilities.expand.excludeRelationshipIds = True
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    return capabilities


def _neo4j_capabilities() -> GraphDbCapabilities:
    capabilities = _bolt_family_capabilities("neo4j")
    # The proxy queries an existing full-text index but never creates or drops one:
    # index management is not part of the proxy contract.
    capabilities.fulltextSearch.supported = True
    capabilities.fulltextSearch.manageIndex = False
    # Neo4j itself has several databases, but a proxy project pins one in its own
    # configuration and the proxy exposes no route to switch — and this flag is
    # what makes the client offer a database switcher. Claiming it would offer a
    # control with nothing behind it.
    capabilities.multiDatabase = False
    capabilities.intents = [*(capabilities.intents or []), "search"]
    assert_capabilities_consistent(capabilities)
    return capabilities


def _memgraph_capabilities() -> GraphDbCapabilities:
    """
    Memgraph speaks Neo4j's bolt protocol, so it inherits the statements unchanged.
    Two things differ: it has no multi-database concept, and no Neo4j-style
    full-text index — ``SHOW INDEXES yield ...`` is a parse error there, so
    declaring search unsupported is what stops it from ever being called.
    """
    capabilities = _bolt_family_capabilities("memgraph")
    # Memgraph has no multi-database concept at all, where Neo4j has one the proxy
    # simply does not expose. Same answer, different reason.
    capabilities.multiDatabase = False
    capabilities.fulltextSearch.supported = False
    capabilities.fulltextSearch.manageIndex = False
    assert_capabilities_consistent(capabilities)
    return capabilities


def _embedded_capabilities(name: str) -> GraphDbCapabilities:
    """
    Kuzu and Ladybug: Cypher over a local file, with RocketGraph's identity problem.

    ``ID(n)`` exists but has no writable literal, so seeds are re-selected by their
    primary key and node ids are ``<Label>:<key>``. Unlike RocketGraph the rest of
    the expand surface is wide: ``label(r)`` filters relationship types, and a
    chained pattern gives real multi-hop.

    Two absences are deliberate. ``both`` is not offered because ``<-[r]->`` is a
    parse error on both engines; ``fulltextSearch`` is not claimed because wiring
    either engine's full-text extension is a separate change, and declaring it here
    would offer the client a control with nothing behind it.
    """
    capabilities = GraphDbCapabilities(type=name, queryLanguages=["cypher"])
    capabilities.identity.nodeId = "label-key"
    capabilities.expand.predicate = "primary-key"
    capabilities.expand.directions = ["all", "from", "to"]
    capabilities.expand.multiHop = True
    capabilities.expand.relationshipTypeFilter = True
    # A key predicate names one end of a traversal; it cannot pin the other.
    capabilities.expand.internalRelationships = False
    # No relationship-identity literal, so an edge cannot be excluded by id.
    capabilities.expand.excludeRelationshipIds = False
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    # An embedded store is one file. Opening another means another project.
    capabilities.multiDatabase = False
    capabilities.fulltextSearch.supported = False
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)
    return capabilities


def _latticedb_capabilities() -> GraphDbCapabilities:
    """
    LatticeDB: an embedded store like Kuzu and Ladybug, with bolt-like identity.

    It sits between the two families rather than inside either. Being a file makes
    it embedded; having ``id(n)`` that both reads *and* matches makes its expand
    surface the wide one -- seeds re-selected by identity, edges excluded by id,
    and a traversal pinned at both ends. Kuzu and Ladybug can do none of those,
    because their ids have no literal to match against.

    Two claims are deliberately withheld:

      - ``fulltextSearch``. LatticeDB really does have BM25 search, and it is the
        only embedded engine here that does -- but the index is populated by
        calling ``fts_index`` per node, so a store nobody indexed answers a search
        with nothing. Claiming it would offer the client a control that silently
        finds nothing on most stores; wiring it means deciding what to do about
        that, which is its own change.
      - ``multiDatabase``. One store is one file, and opening another means another
        project.
    """
    capabilities = GraphDbCapabilities(type="latticedb", queryLanguages=["cypher"])
    capabilities.identity.nodeId = "internal-id"
    capabilities.expand.predicate = "internal-id"
    capabilities.expand.directions = ["all", "from", "to", "both"]
    # Not a grammar limit -- a crash. A chained pattern parses and answers, but the
    # projection this backend needs grows by eight columns a hop, and at that width
    # 0.14.0 corrupts memory: the two-hop statement segfaults the engine process on
    # its third execution. One hop is stable at any width tried, and the client can
    # still walk out a hop at a time.
    capabilities.expand.multiHop = False
    capabilities.expand.relationshipTypeFilter = True
    capabilities.expand.internalRelationships = True
    capabilities.expand.excludeRelationshipIds = True
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.multiDatabase = False
    capabilities.fulltextSearch.supported = False
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)
    return capabilities


def _relational_capabilities(name: str) -> GraphDbCapabilities:
    """
    MSSQL, PostgreSQL and MySQL: a customer's tables served as a graph, one record
    per engine identical modulo the name.

    The shape is RocketGraph's and the embedded family's identity problem arriving for
    a third reason. A relational row has no node identity to re-select a seed by, so
    node ids are ``<Label>:<key>``, an expand pins its seeds with a key predicate, and
    ``schema.source`` has to be ``"proxy"``: the category keys that predicate needs
    come from the mapping, not from the customer's database.

    Three claims are withheld on purpose:

      - ``both`` is not offered as a direction. One statement whose SQL fans out the
        orientations per endpoint pair answers ``all``, so the wide direction is
        served -- and a link row that carries its own endpoints has no orientation to
        lose, which is why Kuzu's reason does not apply but the answer still matches.
      - ``internalRelationships`` and ``excludeRelationshipIds``: a key predicate
        pins one end of a traversal, and an edge minted from its endpoints has no
        identity an ``IN`` could match against.
      - ``write`` and ``fulltextSearch``. This proxy reads a database it does not own,
        and none of the three engines has a full-text index the contract could point
        at without the customer creating one.
    """
    capabilities = GraphDbCapabilities(type=name, queryLanguages=["cypher"])
    capabilities.identity.nodeId = "label-key"
    capabilities.expand.predicate = "primary-key"
    capabilities.expand.directions = ["all", "from", "to"]
    capabilities.expand.multiHop = True
    capabilities.expand.relationshipTypeFilter = True
    capabilities.expand.internalRelationships = False
    capabilities.expand.excludeRelationshipIds = False
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    # The database is one schema of one server, pinned in the project's own config,
    # and no route switches it: claiming this would offer a control with nothing
    # behind it, exactly as for the bolt family.
    capabilities.multiDatabase = False
    capabilities.fulltextSearch.supported = False
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)
    return capabilities


SPANNER_CAPABILITIES = _spanner_capabilities()
BIGQUERY_CAPABILITIES = _bigquery_capabilities()
ROCKETGRAPH_CAPABILITIES = _rocketgraph_capabilities()
NEO4J_CAPABILITIES = _neo4j_capabilities()
MEMGRAPH_CAPABILITIES = _memgraph_capabilities()
KUZU_CAPABILITIES = _embedded_capabilities("kuzu")
LADYBUG_CAPABILITIES = _embedded_capabilities("ladybug")
LATTICEDB_CAPABILITIES = _latticedb_capabilities()
MSSQL_CAPABILITIES = _relational_capabilities("mssql")
POSTGRESQL_CAPABILITIES = _relational_capabilities("postgresql")
MYSQL_CAPABILITIES = _relational_capabilities("mysql")


class GraphIntentSupport(GraphIntentMixin):
    """
    Mixed into a driver alongside ``BaseDatabaseDriver``.

    Categories and key types are read from whatever the driver already caches from
    its last ``get_graph_schema()`` call, so no extra round-trip is introduced.
    """

    graph_capabilities: GraphDbCapabilities
    graph_dialect: StatementDialect

    #: Set by the driver when it builds a graph schema.
    _graph_categories: Dict[str, Any] = {}

    def graph_categories(self) -> Dict[str, Any]:
        return getattr(self, "_graph_categories", {}) or {}

    def graph_key_types(self) -> Dict[str, str]:
        types: Dict[str, str] = {}
        for name, category in self.graph_categories().items():
            keys = (category or {}).get("keys") or []
            key_types = (category or {}).get("keysTypes") or {}
            if keys:
                declared = key_types.get(keys[0])
                if declared:
                    types[name] = str(declared)
        return types

    def remember_graph_categories(self, categories: Dict[str, Any]) -> None:
        """Called by the driver after it resolves the graph schema."""
        self._graph_categories = categories or {}


class SpannerGraphIntents(GraphIntentSupport):
    graph_capabilities = SPANNER_CAPABILITIES
    graph_dialect = SPANNER_DIALECT


class BigQueryGraphIntents(GraphIntentSupport):
    graph_capabilities = BIGQUERY_CAPABILITIES
    graph_dialect = BIGQUERY_DIALECT


class RocketGraphIntents(GraphIntentSupport):
    graph_capabilities = ROCKETGRAPH_CAPABILITIES
    graph_dialect = ROCKETGRAPH_DIALECT


class Neo4jGraphIntents(GraphIntentSupport):
    graph_capabilities = NEO4J_CAPABILITIES
    graph_dialect = NEO4J_DIALECT


class MemgraphGraphIntents(GraphIntentSupport):
    graph_capabilities = MEMGRAPH_CAPABILITIES
    graph_dialect = MEMGRAPH_DIALECT


class KuzuGraphIntents(GraphIntentSupport):
    graph_capabilities = KUZU_CAPABILITIES
    graph_dialect = KUZU_DIALECT


class LadybugGraphIntents(GraphIntentSupport):
    graph_capabilities = LADYBUG_CAPABILITIES
    graph_dialect = LADYBUG_DIALECT


class LatticeDbGraphIntents(GraphIntentSupport):
    graph_capabilities = LATTICEDB_CAPABILITIES
    graph_dialect = LATTICEDB_DIALECT


class RelationalGraphIntents(GraphIntentSupport):
    """
    The family base for the relational engines.

    It adds no behaviour of its own: the categories and key types a ``primary-key``
    backend needs are ``GraphIntentSupport``'s already, and a relational driver feeds
    them from the mapping it rendered as its graph schema. What the subclass carries is
    the *pair* — one engine's capability record and that engine's compiler seam — so
    that adding a fourth relational backend is one of each, and nothing else.
    """


class MssqlGraphIntents(RelationalGraphIntents):
    graph_capabilities = MSSQL_CAPABILITIES
    graph_dialect = MSSQL_DIALECT


class PostgresqlGraphIntents(RelationalGraphIntents):
    graph_capabilities = POSTGRESQL_CAPABILITIES
    graph_dialect = POSTGRESQL_DIALECT


class MysqlGraphIntents(RelationalGraphIntents):
    graph_capabilities = MYSQL_CAPABILITIES
    graph_dialect = MYSQL_DIALECT
