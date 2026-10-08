"""
The pipeline end to end: the same Cypher, three engines' SQL, and one real round trip.

Two mechanisms, because they fail differently. SQL text proves what a server would be *asked*
— quoting, pagination, casts, how a big id list is carried — and only a run proves that the
rows coming back turn into a graph whose edges land on nodes that exist. The run happens on
SQLite, which needs no driver install here; the dialect-specific shapes are asserted in the
text, so nothing in this file depends on a server being reachable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql, mysql, postgresql

from graphxr_database_proxy.drivers.relational import compile as compile_module
from graphxr_database_proxy.drivers.relational import convert, ids, plan, query, schemas
from graphxr_database_proxy.drivers.relational.mapping import from_document
from graphxr_database_proxy.models.graph_mapping import GraphMapping
from graphxr_database_proxy.drivers.relational.engine import ServerInfo
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine

GRAPH_STATEMENT = "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o"
PAGED_STATEMENT = GRAPH_STATEMENT + " SKIP 5 LIMIT 10"
AVERAGE_STATEMENT = "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN o.name, avg(p.score)"
LIKE_STATEMENT = "MATCH (p:Person) WHERE p.name CONTAINS '50%' RETURN p"

NODES = {
    "Person": schemas.NodeSchema(
        label="Person",
        table="Person",
        key="person_id",
        key_type="bigint",
        props={"person_id": "bigint", "name": "varchar(50)", "hired": "date", "score": "int"},
    ),
    "Organization": schemas.NodeSchema(
        label="Organization",
        table="Organization",
        key="organization_id",
        key_type="bigint",
        props={"organization_id": "bigint", "name": "varchar(50)"},
    ),
}


def _pair(src: str, src_column: str, dst: str, dst_column: str) -> schemas.Pair:
    return schemas.Pair(
        src=schemas.Endpoint(src, src_column, "bigint"),
        dst=schemas.Endpoint(dst, dst_column, "bigint"),
        row_estimate=2,
    )


EDGES = {
    "EMPLOYMENT": schemas.RelSchema(
        type="EMPLOYMENT",
        table="Employment",
        key="employment_id",
        key_type="bigint",
        endpoints=[_pair("Person", "person_id", "Organization", "organization_id")],
        props={"employment_id": "bigint", "since": "date"},
    ),
    "KNOWS": schemas.RelSchema(
        type="KNOWS",
        table="Knows",
        key=None,
        key_type=None,
        endpoints=[_pair("Person", "a_id", "Person", "b_id")],
        props={},
    ),
}

#: One engine, its dialect, and the server the bulk forms are chosen against.
ENGINES = {
    "mssql": (MSSQLEngine(), mssql.dialect(), ServerInfo(engine="mssql", major=15, minor=0, compat_level=150)),
    "postgresql": (PostgreSQLEngine(), postgresql.dialect(), ServerInfo(engine="postgresql", major=16, minor=4)),
    "mysql": (MySQLEngine(), mysql.dialect(), ServerInfo(engine="mysql", major=8, minor=0, build=4)),
}


def _branch(
    statement: str, index: int = 0, nodes: Optional[Dict[str, Any]] = None
) -> "tuple[Any, Any]":
    """``(prepared, branch)`` — the two steps the driver runs before it compiles anything."""
    prepared = query.prepare(statement, default_limit=0)
    branches = plan.concrete_plans(prepared.plan, NODES if nodes is None else nodes, EDGES)
    assert branches, "a pattern this mapping cannot answer is a fixture bug, not a test"
    return prepared, branches[index]


def for_engine(statement: str, name: str, index: int = 0) -> "compile_module.CompiledStatement":
    spelling, _, server = ENGINES[name]
    prepared, branch = _branch(statement, index)
    return compile_module.compile_statement(branch, NODES, EDGES, spelling=spelling, server=server, spec=prepared.spec)


def text(statement: str, name: str, index: int = 0) -> str:
    """The SQL one engine would be sent for one statement."""
    _, dialect, _ = ENGINES[name]
    return str(for_engine(statement, name, index).select.compile(dialect=dialect)).replace("\n", " ")


# -- the same plan, three engines --


@pytest.mark.parametrize("name", sorted(ENGINES))
def test_the_projection_labels_are_the_same_whatever_the_engine_quotes_like(name: str):
    """
    ``convert`` reads columns by their ``__gx_`` label, so the label must not move per engine.

    If a dialect's quoting ever reached a label, the row reader would look for a column the
    result set calls something else and answer an empty graph.
    """
    compiled = for_engine(GRAPH_STATEMENT, name)
    assert "__gx_v0_k0" in text(GRAPH_STATEMENT, name)
    assert compiled.columns == ("__gx_v0_k0", "__gx_v0_p0", "__gx_v0_p1", "__gx_v0_p2", "__gx_v1_k0", "__gx_v1_p0", "__gx_e0_k0", "__gx_e0_p0")
    assert compiled.manifest.edges[0].start_var == "p"


def test_each_engine_gets_its_own_identifier_quoting():
    assert '"Person" AS v0' in text(GRAPH_STATEMENT, "postgresql")
    assert "`Person` AS v0" in text(GRAPH_STATEMENT, "mysql")
    assert "[Person] AS v0" in text(GRAPH_STATEMENT, "mssql")


def test_each_engine_paginates_the_way_its_grammar_demands():
    """
    And SQL Server only paginates at all because the compiler ordered by the key columns.

    Without those the dialect raises before a statement exists — see
    ``test_sql_server_cannot_page_at_all_without_an_order_by`` in the compile tests.
    """
    paged = text(PAGED_STATEMENT, "postgresql").replace("::INTEGER", "")
    assert "ORDER BY v0.person_id ASC" in paged and "LIMIT %(param_1)s OFFSET %(param_2)s" in paged
    assert "LIMIT %s, %s" in text(PAGED_STATEMENT, "mysql")
    mssql_text = text(PAGED_STATEMENT, "mssql")
    assert "ROW_NUMBER() OVER (ORDER BY v0.person_id ASC" in mssql_text


def test_a_server_new_enough_to_answer_offset_fetch_is_paged_with_it():
    dialect = mssql.dialect()
    dialect._supports_offset_fetch = True  # type: ignore[attr-defined]
    compiled = for_engine(PAGED_STATEMENT, "mssql")
    rendered = str(compiled.select.compile(dialect=dialect)).replace("\n", " ")
    assert "OFFSET :param_1 ROWS  FETCH FIRST :param_2 ROWS ONLY" in rendered


def test_an_integer_average_is_cast_only_where_the_default_is_integer_division():
    assert "avg(CAST(v0.score AS DECIMAL(38, 10)))" in text(AVERAGE_STATEMENT, "mssql")
    assert "avg(v0.score)" in text(AVERAGE_STATEMENT, "postgresql")
    assert "CAST" not in text(AVERAGE_STATEMENT, "mysql")


def test_a_big_selection_is_carried_the_only_way_each_engine_can_carry_it():
    """One parameter each: the alternative is two thousand, and a statement that long is a plan-cache miss."""
    big = ", ".join(str(index) for index in range(2001))
    statement = "MATCH (p:Person) WHERE p.person_id IN [%s] RETURN p" % big
    postgres = text(statement, "postgresql")
    assert "= ANY (" in postgres and "::BIGINT[]" in postgres
    assert len(for_engine(statement, "postgresql").parameters) == 1

    sql_server = text(statement, "mssql")
    assert "OPENJSON(:gx_json1) WITH ([value] bigint" in sql_server
    mysql_text = text(statement, "mysql")
    assert "JSON_TABLE(%s, '$[*]' COLUMNS (gx_value BIGINT PATH '$'))" in mysql_text


def test_a_selection_small_enough_for_every_engine_is_an_inline_in():
    statement = "MATCH (p:Person) WHERE p.person_id IN [1001, 1002] RETURN p"
    for name in sorted(ENGINES):
        rendered = text(statement, name)
        assert " IN (" in rendered
        assert for_engine(statement, name).parameters == {"gx_p1": 1001, "gx_p2": 1002}


def test_a_like_wildcard_in_a_value_is_escaped_on_every_engine():
    for name in sorted(ENGINES):
        assert "ESCAPE" in text(LIKE_STATEMENT, name)


# -- the round trip --

_SCHEMA = (
    "CREATE TABLE Person (person_id INTEGER PRIMARY KEY, name TEXT, hired TEXT, score INTEGER)",
    "CREATE TABLE Organization (organization_id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE Employment (employment_id INTEGER PRIMARY KEY, person_id INTEGER, organization_id INTEGER, since TEXT)",
    "CREATE TABLE Knows (a_id INTEGER, b_id INTEGER)",
    # Spaced on purpose: the name a real order-entry schema arrives with, and the thing
    # the loader used to refuse. SQLite quotes both with "".
    'CREATE TABLE "Order Items" ("order item id" INTEGER PRIMARY KEY, "unit price" TEXT)',
)
_ROWS = (
    "INSERT INTO Person VALUES (1001, 'Avery', '1984-03-01', 7), (1002, 'Bo', '1990-07-07', 3), (1003, 'O''Neil', '2001-01-01', NULL)",
    "INSERT INTO Organization VALUES (2001, 'Acme'), (2002, 'Globex')",
    "INSERT INTO Employment VALUES (11, 1001, 2001, '2024-01-31'), (12, 1001, 2002, '2025-02-01'), (13, 1002, 2001, '2026-03-03')",
    "INSERT INTO Knows VALUES (1001, 1002), (1002, 1001)",
    'INSERT INTO "Order Items" VALUES (3001, \'12.50\'), (3002, \'7.00\')',
)


@pytest.fixture(scope="module")
def library() -> Any:
    """
    A tiny Person / Organization / Employment schema, in memory, with the rows below.

    The PostgreSQL spelling is what the statement is *built* with — it is the engine whose
    type decisions the compiler is being exercised on — while the dialect that renders it is
    SQLite's, chosen by the connection. Nothing in the pipeline needs a driver this box has.
    """
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        for statement in _SCHEMA + _ROWS:
            connection.exec_driver_sql(statement)
        yield connection
    engine.dispose()


def run(
    library: Any,
    statement: str,
    index: int = 0,
    nodes: Optional[Dict[str, Any]] = None,
) -> convert.GraphConversion:
    """One branch, executed for real, and read back as a graph."""
    prepared, branch = _branch(statement, index, nodes)
    compiled = compile_module.compile_statement(
        branch,
        NODES if nodes is None else nodes,
        EDGES,
        spelling=PostgreSQLEngine(),
        spec=prepared.spec,
    )
    rows = [dict(row._mapping) for row in library.execute(compiled.select, compiled.parameters)]
    return convert.convert_rows(rows, compiled.manifest)


def node_ids(result: convert.GraphConversion) -> List[str]:
    return [node.id for node in result.graph.nodes]


def edge_ids(result: convert.GraphConversion) -> List[str]:
    return [edge.id for edge in result.graph.relationships]


def test_an_expand_round_trip_answers_the_nodes_and_edges_the_rows_hold(library: Any):
    result = run(library, "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) WHERE p.person_id = 1001 RETURN p, r, o")
    assert node_ids(result) == ["Person:1001", "Organization:2001", "Organization:2002"]
    assert edge_ids(result) == ["EMPLOYMENT:11", "EMPLOYMENT:12"]
    assert result.summary == {}
    person = result.graph.nodes[0]
    assert person.properties["name"] == "Avery" and person.properties["hired"] == "1984-03-01"


def test_a_minted_id_selects_the_row_it_was_minted_from(library: Any):
    """
    The identity invariant of this whole surface, run against real rows.

    ``intents.py`` re-selects a seed from the key half of an id it was handed, so an id whose
    key half does not match the column again does not widen a graph — it finds nothing, and
    looks like a database with no more data in it.
    """
    first = run(library, "MATCH (p:Person) RETURN p")
    seeds = [ids.decode(node_id) for node_id in node_ids(first)]
    assert seeds == [("Person", "1001"), ("Person", "1002"), ("Person", "1003")]
    keys = ", ".join(str(ids.coerce_key(text, "bigint")) for _, text in seeds)
    again = run(library, "MATCH (p:Person) WHERE p.person_id IN [%s] RETURN p" % keys)
    assert node_ids(again) == node_ids(first)


def test_a_value_with_a_quote_in_it_is_bound_and_not_written_into_the_text(library: Any):
    """The proof that the injection defence is real: the row comes back, nothing blows up."""
    result = run(library, "MATCH (p:Person) WHERE p.name = \"O'Neil\" RETURN p")
    assert node_ids(result) == ["Person:1003"]


#: The file a person would have to write for the spaced table below, with no catalog to
#: propose it: this is the document the loader used to refuse, override and all.
SPACED_DOCUMENT = {
    "version": 1,
    "nodes": [
        {
            "label": "LineItem",
            "table": "Order Items",
            "key": "order item id",
            "properties": {"order item id": "int", "unit price": "varchar(20)"},
        }
    ],
    "edges": [],
}


def test_a_table_and_a_column_the_engine_spells_with_a_space_answer_rows(library: Any):
    """
    The chain end to end, from the file a person writes to the rows a canvas draws.

    The schema this runs on is not this file's ``NODES`` fixture — it is what
    ``from_document`` agreed to, so the loader's spelling, the compiler's quoting, the
    server's answer and the ids minted from the rows all sit under one assertion. A fixture
    could have claimed anything about a spaced name; this claims nothing the loader refused.
    """
    loaded = from_document(GraphMapping.model_validate(SPACED_DOCUMENT), None)
    assert loaded.problems == [], loaded.problems
    assert loaded.nodes["LineItem"].table == "Order Items"
    assert loaded.nodes["LineItem"].key == "order item id"

    result = run(library, "MATCH (l:LineItem) RETURN l", nodes=loaded.nodes)
    assert node_ids(result) == ["LineItem:3001", "LineItem:3002"]
    assert result.graph.nodes[0].properties["unit price"] == "12.50"

    seeded = run(
        library, 'MATCH (l:LineItem) WHERE l.`unit price` = "12.50" RETURN l', nodes=loaded.nodes
    )
    assert node_ids(seeded) == ["LineItem:3001"]

    again = run(
        library,
        "MATCH (l:LineItem) WHERE l.`order item id` IN [3001, 3002] RETURN l",
        nodes=loaded.nodes,
    )
    assert node_ids(again) == node_ids(result)


def test_the_limit_is_applied_by_the_server_and_the_page_is_stable(library: Any):
    result = run(library, "MATCH (p:Person) RETURN p LIMIT 2")
    assert len(result.graph.nodes) == 2
    # The tiebreakers are what make the second page a different pair of rows.
    first = node_ids(run(library, "MATCH (p:Person) RETURN p SKIP 0 LIMIT 2"))
    second = node_ids(run(library, "MATCH (p:Person) RETURN p SKIP 2 LIMIT 2"))
    assert first == ["Person:1001", "Person:1002"] and second == ["Person:1003"]


def test_reverse_edge_endpoint_ids_still_match_their_nodes(library: Any):
    """
    The pattern runs against the mapping, so the answer has to be read back the mapping's way.

    Every id an edge names must be a node the same answer carries — that is the whole of the
    GraphXR contract — and an edge drawn from an arrow the person wrote backwards has to keep
    pointing from the record its source column holds, not from whichever end the pattern
    happened to start at.
    """
    result = run(library, "MATCH (o:Organization)<-[r:EMPLOYMENT]-(p:Person) RETURN o, r, p")
    present = set(node_ids(result))
    by_id = {node.id: node for node in result.graph.nodes}
    assert len(result.graph.relationships) == 3
    assert result.summary == {}, "an edge that could not be placed would be a silently smaller graph"
    for edge in result.graph.relationships:
        assert edge.startNodeId in present and edge.endNodeId in present
        assert "Person" in by_id[edge.startNodeId].labels
        assert "Organization" in by_id[edge.endNodeId].labels
    employment = {edge.id: (edge.startNodeId, edge.endNodeId) for edge in result.graph.relationships}
    assert employment["EMPLOYMENT:11"] == ("Person:1001", "Organization:2001")
    assert employment["EMPLOYMENT:13"] == ("Person:1002", "Organization:2001")


def test_an_undirected_pattern_runs_one_branch_per_direction_and_answers_the_same_lines(library: Any):
    """
    Both orientations, two statements, one edge id each — because the branch is chosen before
    the statement is built, and the id is minted from the row, not from the arrow.
    """
    forward = run(library, "MATCH (a:Person)-[r:KNOWS]-(b:Person) RETURN a, r, b", index=0)
    backward = run(library, "MATCH (a:Person)-[r:KNOWS]-(b:Person) RETURN a, r, b", index=1)
    assert edge_ids(forward) == edge_ids(backward) == ["KNOWS:1001|1002"]
    assert (forward.graph.relationships[0].startNodeId, forward.graph.relationships[0].endNodeId) == (
        "Person:1001",
        "Person:1002",
    )
    assert (backward.graph.relationships[0].startNodeId, backward.graph.relationships[0].endNodeId) == (
        "Person:1001",
        "Person:1002",
    )


def test_a_scalar_answer_comes_back_as_a_two_dimensional_table_with_headers(library: Any):
    prepared = query.prepare(AVERAGE_STATEMENT, default_limit=0)
    branch = plan.concrete_plans(prepared.plan, NODES, EDGES)[0]
    compiled = compile_module.compile_statement(branch, NODES, EDGES, spelling=PostgreSQLEngine(), spec=prepared.spec)
    rows = [dict(row._mapping) for row in library.execute(compiled.select, compiled.parameters)]
    table = convert.rows_to_table(rows, compiled.columns)
    assert table[0] == ["o.name", "avg_p_score"]
    assert sorted(row[1] for row in table[1:]) == [5.0, 7.0]


def test_a_join_the_library_has_no_row_for_answers_an_empty_graph_not_an_error(library: Any):
    result = run(library, "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) WHERE p.person_id = 9999 RETURN p, r, o")
    assert result.graph.nodes == [] and result.graph.relationships == []
    assert result.summary == {}
