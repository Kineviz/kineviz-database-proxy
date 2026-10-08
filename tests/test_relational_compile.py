"""
One branch in, one statement out — and the statement has to be the one this engine runs.

What is asserted here is what a person cannot see in the answer: which table a label came
from, which end of an edge the manifest calls its start, whether a page is stable, and
whether SQL Server was handed the ``ORDER BY`` its ``OFFSET/FETCH`` grammar requires.
Every one of those is a decision the compiler makes silently, and a wrong one still
returns rows.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql, mysql, postgresql
from sqlalchemy.exc import CompileError as SACompileError
from sqlalchemy.sql import visitors

from graphxr_database_proxy.drivers.relational import compile as compile_module
from graphxr_database_proxy.drivers.relational import plan, query
from graphxr_database_proxy.drivers.relational.compile import (
    CompileError,
    RepeatedRelationshipVariable,
    compile_statement,
    compile_statements,
    edge_columns,
)
from graphxr_database_proxy.drivers.relational.engine import ServerInfo
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from graphxr_database_proxy.drivers.relational.schemas import Endpoint, NodeSchema, Pair, RelSchema

PERSON_PROPS = {
    "person_id": "bigint",
    "name": "varchar(50)",
    "score": "int",
    "blurb": "text",
}
ORGANIZATION_PROPS = {"organization_id": "bigint", "name": "varchar(50)"}

NODES = {
    "Person": NodeSchema(label="Person", table="T_PERSON", key="person_id", key_type="bigint", props=dict(PERSON_PROPS)),
    "Organization": NodeSchema(
        label="Organization",
        table="Organization",
        key="organization_id",
        key_type="bigint",
        props=dict(ORGANIZATION_PROPS),
    ),
}


def pair(src_label: str, src_column: str, dst_label: str, dst_column: str, estimate: int = 1) -> Pair:
    return Pair(
        src=Endpoint(src_label, src_column, "bigint"),
        dst=Endpoint(dst_label, dst_column, "bigint"),
        row_estimate=estimate,
    )


EDGES = {
    "EMPLOYMENT": RelSchema(
        type="EMPLOYMENT",
        table="Employment",
        key="employment_id",
        key_type="bigint",
        endpoints=[pair("Person", "person_id", "Organization", "organization_id", 120)],
        props={"employment_id": "bigint", "since": "datetime"},
    ),
    # A self-join, which is what makes relationship uniqueness a SQL problem at all.
    "KNOWS": RelSchema(
        type="KNOWS",
        table="Knows",
        key="knows_id",
        key_type="bigint",
        endpoints=[pair("Person", "a_id", "Person", "b_id")],
        props={"knows_id": "bigint", "since": "datetime"},
    ),
    # An association table with no primary key of its own.
    "ENROLLS": RelSchema(
        type="ENROLLS",
        table="Enrollments",
        key=None,
        key_type=None,
        endpoints=[pair("Person", "student_id", "Person", "mentor_id")],
        props={"grade": "varchar(2)"},
    ),
}

MSSQL = mssql.dialect()
POSTGRESQL = postgresql.dialect()
MYSQL = mysql.dialect()


def _text(statement, dialect) -> str:
    return str(statement.compile(dialect=dialect)).replace("\n", " ")


class Rendered:
    """
    One compiled statement plus its SQL text for one dialect.

    ``CompiledStatement`` is frozen — deliberately, since it is handed to a caller that must
    not be able to restamp its own limits — so the text a test asserts on is held beside it
    rather than written onto it.
    """

    def __init__(self, compiled: "compile_module.CompiledStatement", dialect) -> None:
        self.compiled = compiled
        self.sql = _text(compiled.select, dialect)

    def __getattr__(self, name: str) -> object:
        return getattr(self.compiled, name)


def compile_one(
    statement: str,
    spelling=MSSQLEngine(),
    dialect=MSSQL,
    *,
    nodes: Optional[Dict[str, NodeSchema]] = None,
    edges: Optional[Dict[str, RelSchema]] = None,
    server: Optional[ServerInfo] = None,
    index: int = 0,
) -> Rendered:
    """The whole front door: guard, plan, branch, compile — the way the driver will."""
    prepared = query.prepare(statement, default_limit=0)
    branches = plan.concrete_plans(prepared.plan, nodes or NODES, edges or EDGES)
    assert branches, "the fixture plans to nothing, so it cannot compile to anything either"
    compiled = compile_statement(
        branches[index], nodes or NODES, edges or EDGES, spelling=spelling, server=server, spec=prepared.spec
    )
    return Rendered(compiled, dialect)


def sql(statement: str, spelling=MSSQLEngine(), dialect=MSSQL, **kwargs) -> str:
    return compile_one(statement, spelling, dialect, **kwargs).sql


# -- the projection and the manifest --


#: One label with a column SQL Server's vocabulary knows and its type table does not name.
UNNAMEABLE_NODES = {
    "Detail": NodeSchema(
        label="Detail",
        table="T_DETAIL",
        key="id",
        key_type="bigint",
        props={"id": "bigint", "doc": "xml"},
    )
}


def test_a_column_the_engine_cannot_name_is_declared_and_not_refused():
    """
    The projection is the one duty that may not refuse: ``doc`` is read out and never sent
    in, so its unnamed type costs a decode, not a wrong answer, and the statement still
    carries it as text. ``NullType`` would be the literal truth and would change the SQL of
    anything compared against it, so the declaration says which duty it is doing.

    The same column in a predicate does refuse, from the same front door — that half is what
    keeps the projection honest rather than an excuse to drop the guard.
    """
    compiled = compile_one("MATCH (d:Detail) RETURN d", MSSQLEngine(), MSSQL, nodes=UNNAMEABLE_NODES)
    projected = [
        element
        for element in visitors.iterate(compiled.select)
        if isinstance(element, sa.Column) and element.name == "doc"
    ]
    assert projected, compiled.sql
    assert all(isinstance(column.type, sa.String) for column in projected), [column.type for column in projected]
    with pytest.raises(compile_module.UnbindableType):
        compile_one("MATCH (d:Detail) WHERE d.doc = 'x' RETURN d", MSSQLEngine(), MSSQL, nodes=UNNAMEABLE_NODES)


def test_every_projected_column_is_named_and_the_manifest_says_what_it_means():
    compiled = compile_one(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o", PostgreSQLEngine(), POSTGRESQL
    )
    vertex, organization = compiled.manifest.vertices
    assert compiled.kind == "graph"
    assert compiled.columns == (
        vertex.key_column,
        "__gx_v0_p0",
        "__gx_v0_p1",
        "__gx_v0_p2",
        organization.key_column,
        "__gx_v1_p0",
        compiled.manifest.edges[0].key_column,
        "__gx_e0_p0",
    )
    assert vertex.properties == {"__gx_v0_p0": "name", "__gx_v0_p1": "score", "__gx_v0_p2": "blurb"}
    assert compiled.sql.count("__gx_v0_p") == 3


def test_the_key_is_projected_once_and_is_not_repeated_as_a_property():
    """``convert`` shows the key under its own name; projecting it twice would be two columns."""
    compiled = compile_one("MATCH (p:Person) RETURN p", PostgreSQLEngine(), POSTGRESQL)
    vertex = compiled.manifest.vertices[0]
    assert vertex.key_column == "__gx_v0_k0"
    assert vertex.key_name == "person_id"
    assert "person_id" not in vertex.properties.values()
    assert compiled.sql.count("v0.person_id") == 1


def test_a_table_name_comes_from_the_mapping_and_nothing_is_reflected():
    """
    The label is ``Person`` and the table is ``T_PERSON``.

    Nothing here talks to a database, so the only way a table can be known is the mapping.
    A reflected schema would also mean the compiler could disagree with the catalog that
    produced the mapping.
    """
    rendered = sql("MATCH (p:Person) RETURN p", MSSQLEngine(), MSSQL)
    assert "[T_PERSON] AS v0" in rendered
    assert "Person" not in rendered.replace("[T_PERSON]", "")
    compiled = compile_one("MATCH (p:Person) RETURN p", MSSQLEngine(), MSSQL)
    assert compiled.manifest.vertices[0].label == "Person"


def test_the_edge_endpoints_follow_the_mapping_not_the_arrow():
    """
    ``<-[]-`` reads the link backwards, so the row's own columns decide which end is which.

    The manifest's ``start_var`` has to name the variable the *source* key column points at,
    or every reversed edge would carry a ``startNodeId`` of a node it does not start at.
    """
    compiled = compile_one(
        "MATCH (o:Organization)<-[r:EMPLOYMENT]-(p:Person) RETURN o, r, p", PostgreSQLEngine(), POSTGRESQL
    )
    edge = compiled.manifest.edges[0]
    assert edge.start_var == "p" and edge.end_var == "o"
    assert edge.start_label == "Person" and edge.end_label == "Organization"
    # The pattern reads right to left, so the hop that leaves the root is the mapping's dst.
    assert "e0.organization_id = v0.organization_id" in compiled.sql
    assert "v1.person_id = e0.person_id" in compiled.sql


def test_an_edge_with_no_key_of_its_own_projects_no_key_column():
    compiled = compile_one("MATCH (a:Person)-[e:ENROLLS]->(b:Person) RETURN a, e, b", MSSQLEngine(), MSSQL)
    edge = compiled.manifest.edges[0]
    assert edge.key_column is None and edge.key_name is None
    assert "__gx_e0_k0" not in compiled.sql
    assert edge.properties == {"__gx_e0_p0": "grade"}


def test_edge_columns_lists_the_join_columns_as_well_as_the_properties():
    """Endpoint columns are not properties, but a predicate may still name one."""
    schema = EDGES["EMPLOYMENT"]
    columns = edge_columns(schema, schema.endpoints[0])
    assert columns["employment_id"] == "bigint"
    assert columns["since"] == "datetime"
    assert columns["person_id"] == "bigint" and columns["organization_id"] == "bigint"


# -- joins, cycles and uniqueness --


def test_two_hops_over_one_link_table_may_not_use_the_same_relationship_twice():
    """
    Cypher forbids it, a plain ``JOIN`` does not.

    Without the inequality the answer includes every ``a -> b -> a`` bounce-back, which is
    a wrong row rather than an empty one, and nothing downstream could tell.
    """
    rendered = sql("MATCH (a:Person)-[r:KNOWS]->(b)-[r2:KNOWS]->(c:Person) RETURN a, r, b, r2, c")
    assert "e1.knows_id != e0.knows_id" in rendered


def test_a_variable_already_in_the_from_clause_is_closed_by_a_predicate_not_a_second_join():
    """
    The cycle ``(a)-[r]->(b)-[r2]->(a)`` needs one alias for ``a``, used twice.

    A second alias for the same variable would be a second node in the answer, and a person
    would see the same record twice on the canvas under two ids.
    """
    compiled = compile_one("MATCH (a:Person)-[r:KNOWS]->(b)-[r2:KNOWS]->(a) RETURN a, r, b, r2", MSSQLEngine(), MSSQL)
    assert compiled.sql.count("[T_PERSON] AS v0") == 1
    assert "v0.person_id = e1.b_id" in compiled.sql
    assert len(compiled.manifest.vertices) == 2


def test_one_relationship_variable_used_twice_is_refused():
    with pytest.raises(RepeatedRelationshipVariable) as refused:
        compile_one(
            "MATCH (a:Person)-[r:KNOWS]->(b)<-[r:KNOWS]-(c:Person) RETURN a, r, b", MSSQLEngine(), MSSQL
        )
    assert "'r'" in str(refused.value)


# -- paging --


def test_an_unpaged_graph_carries_no_order_by():
    rendered = sql("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o")
    assert "ORDER BY" not in rendered


def test_a_paged_graph_ends_in_the_key_of_every_variable_so_the_page_is_stable():
    compiled = compile_one(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o LIMIT 10", MSSQLEngine(), MSSQL
    )
    assert "ORDER BY v0.person_id ASC, v1.organization_id ASC, e0.employment_id ASC" in compiled.sql


def test_sql_server_cannot_page_at_all_without_an_order_by():
    """
    The engine fact the tiebreakers exist for, asserted on SQLAlchemy rather than trusted.

    ``OFFSET/FETCH`` needs an order, and ``ORDER BY (SELECT 1)`` — the dummy the reference
    bridge emitted — is exactly the thing that makes a page meaningless, so the compiler
    supplies real keys instead. A statement with no order and no keys is refused by the
    dialect itself.
    """
    paged = sa.select(sa.column("one")).limit(10).offset(5)
    with pytest.raises(SACompileError) as refused:
        str(paged.compile(dialect=MSSQL))
    assert "order_by" in str(refused.value) or "ORDER BY" in str(refused.value)
    # And ours compiles, because the key columns went in.
    assert "ROW_NUMBER() OVER (ORDER BY v0.person_id ASC" in sql(
        "MATCH (p:Person) RETURN p SKIP 5 LIMIT 10"
    )


def test_a_server_that_answers_offset_fetch_gets_offset_fetch():
    dialect = mssql.dialect()
    dialect._supports_offset_fetch = True  # type: ignore[attr-defined]
    compiled = compile_one("MATCH (p:Person) RETURN p SKIP 5 LIMIT 10", MSSQLEngine(), dialect)
    assert "OFFSET :param_1 ROWS" in compiled.sql
    assert "FETCH FIRST :param_2 ROWS ONLY" in compiled.sql


def test_a_page_with_no_offset_is_a_top_and_needs_no_order():
    rendered = sql("MATCH (p:Person) RETURN p.name LIMIT 2")
    assert "TOP __[POSTCOMPILE_param_1]" in rendered
    assert "ORDER BY" not in rendered


@pytest.mark.parametrize(
    "dialect,spelling,expected",
    [
        (POSTGRESQL, PostgreSQLEngine(), "LIMIT %(param_1)s::INTEGER"),
        (MYSQL, MySQLEngine(), "LIMIT %s"),
        (MSSQL, MSSQLEngine(), "TOP __[POSTCOMPILE_param_1]"),
    ],
)
def test_the_page_is_written_the_way_each_engine_reads_it(dialect, spelling, expected):
    # PostgreSQL's optional INTEGER bind casts differ between SQLAlchemy 2.0/2.1.
    assert expected.replace("::INTEGER", "") in sql("MATCH (p:Person) RETURN p LIMIT 25", spelling, dialect).replace("::INTEGER", "")


def test_an_explicit_order_comes_first_and_the_keys_after():
    """The tiebreakers go last, or the caller's order would be the one that decides pages."""
    ordered = compile_one(
        "MATCH (p:Person) RETURN p ORDER BY p.name DESC LIMIT 5", PostgreSQLEngine(), POSTGRESQL
    )
    assert "ORDER BY v0.name DESC, v0.person_id ASC" in ordered.sql


def test_an_order_by_naming_a_variable_the_pattern_does_not_bind_is_refused():
    with pytest.raises(CompileError) as refused:
        compile_one("MATCH (p:Person) RETURN p ORDER BY z.name LIMIT 5", MSSQLEngine(), MSSQL)
    assert "ORDER BY" in str(refused.value)


# -- values, aggregates and casts --


@pytest.mark.parametrize("spelling,dialect", [
    (MSSQLEngine(), MSSQL), (PostgreSQLEngine(), POSTGRESQL), (MySQLEngine(), MYSQL),
])
def test_relationship_counts_use_the_relationship_key(spelling, dialect):
    rendered = sql(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN count(r)",
        spelling, dialect,
    )
    assert "count(e0.employment_id)" in rendered


def test_counting_a_keyless_relationship_counts_the_matched_rows():
    rendered = sql("MATCH (p:Person)-[r:ENROLLS]->(m:Person) RETURN count(r)")
    assert "count(*)" in rendered


def test_distinct_keyless_relationship_count_is_refused():
    with pytest.raises(query.RefusedQuery, match="DISTINCT inside an aggregate"):
        sql("MATCH (p:Person)-[r:ENROLLS]->(m:Person) RETURN count(DISTINCT r)")


def test_an_average_of_integers_is_cast_on_sql_server_and_nowhere_else():
    """
    ``avg(int)`` answers with integer division on SQL Server; on MySQL it does not.

    The cast is the engine's answer, not a guess at the column's type, which is why it can
    differ per function as well as per engine.
    """
    statement = "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN o.name, avg(p.score)"
    assert "avg(CAST(v0.score AS DECIMAL(38, 10)))" in sql(statement, MSSQLEngine(), MSSQL)
    assert "CAST" not in sql(statement, PostgreSQLEngine(), POSTGRESQL)
    assert "CAST" not in sql(statement, MySQLEngine(), MYSQL)


def test_a_count_is_never_cast():
    """Casting the column changes what ``count`` means, so the hook is not applied to it."""
    rendered = sql("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN o.name, count(p.score)", MSSQLEngine(), MSSQL)
    assert "CAST" not in rendered
    assert "count(v0.score)" in rendered


def test_a_grouping_key_is_projected_as_the_expression_it_is_grouped_by():
    """
    On SQL Server an unbounded ``text`` cannot be grouped at all, so it is narrowed.

    Narrowing the projection but grouping the bare column would name something the
    ``GROUP BY`` never mentioned, which is a syntax error, not a different answer.
    """
    rendered = sql("MATCH (p:Person) RETURN p.blurb, count(*)", MSSQLEngine(), MSSQL)
    assert "SELECT CAST(v0.blurb AS NVARCHAR(450)) AS [p.blurb]" in rendered
    assert "GROUP BY CAST(v0.blurb AS NVARCHAR(450))" in rendered
    assert "GROUP BY v0.blurb" not in rendered


def test_a_grouped_answer_names_its_columns_in_order():
    compiled = compile_one(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN o.name, count(*)", PostgreSQLEngine(), POSTGRESQL
    )
    assert compiled.kind == "aggregate"
    assert compiled.columns == ("o.name", "count_all")
    assert compiled.manifest.vertices == ()


def test_a_scalar_return_projects_only_what_it_was_asked_for():
    compiled = compile_one("MATCH (p:Person) RETURN p.name, p.score", PostgreSQLEngine(), POSTGRESQL)
    assert compiled.kind == "scalar"
    assert compiled.columns == ("p.name", "p.score")
    assert "GROUP BY" not in compiled.sql


def test_a_distinct_return_reaches_the_statement():
    compiled = compile_one("MATCH (p:Person) RETURN DISTINCT p.name", PostgreSQLEngine(), POSTGRESQL)
    assert compiled.distinct is True
    assert "SELECT DISTINCT" in compiled.sql


# -- binds --


def test_the_parameters_are_the_binds_the_statement_names():
    compiled = compile_one(
        "MATCH (p:Person) WHERE p.name = 'Avery' AND p.score > 3 RETURN p", PostgreSQLEngine(), POSTGRESQL
    )
    assert compiled.parameters == {"gx_p1": "Avery", "gx_p2": 3}
    assert "Avery" not in compiled.sql and "3" not in compiled.sql.split("WHERE")[1]


def test_a_predicate_on_a_join_column_narrows_a_polymorphic_link():
    compiled = compile_one(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) WHERE r.organization_id = 7 RETURN p, r, o",
        PostgreSQLEngine(),
        POSTGRESQL,
    )
    assert "e0.organization_id =" in compiled.sql
    assert compiled.parameters == {"gx_p1": 7}


# -- fan-out --


def test_a_fan_out_compiles_one_statement_per_chunk_and_says_which():
    server = ServerInfo(engine="mssql", major=12, minor=0, compat_level=110)
    prepared = query.prepare("MATCH (p:Person) WHERE p.person_id IN [%s] RETURN p" % _ids(2001), default_limit=0)
    branch = plan.concrete_plans(prepared.plan, NODES, EDGES)[0]
    statements: List[compile_module.CompiledStatement] = compile_statements(
        branch, NODES, EDGES, spelling=MSSQLEngine(), server=server, spec=prepared.spec
    )
    assert len(statements) == 3
    assert [item.chunk_count for item in statements] == [3, 3, 3]
    assert [item.chunk_index for item in statements] == [0, 1, 2]
    assert [item.fanned_out for item in statements] == [True, True, True]
    assert [len(item.parameters) for item in statements] == [1000, 1000, 1]
    seen = sorted(value for item in statements for value in item.parameters.values())
    assert seen == list(range(2001))


def test_a_statement_that_needs_no_fan_out_is_one_statement():
    prepared = query.prepare("MATCH (p:Person) WHERE p.person_id IN [1, 2, 3] RETURN p", default_limit=0)
    branch = plan.concrete_plans(prepared.plan, NODES, EDGES)[0]
    statements = compile_statements(branch, NODES, EDGES, spelling=MSSQLEngine(), spec=prepared.spec)
    assert len(statements) == 1
    assert statements[0].fanned_out is False
    assert statements[0].chunk_count == 1


def _ids(count: int) -> str:
    return ", ".join(str(index) for index in range(count))


def test_no_type_marker_set_lives_only_in_mapping_index() -> None:
    """
    Pin: ``compile._is_no_type_marker`` must read from ``mapping_index.NO_TYPE``.

    The key-refusal path (here) and the property-path (``mapping_props`` via
    ``mapping_index.names_a_type``) both depend on the same set. A private copy in
    ``compile.py`` would let the two drift the next time the public set grows, and no
    other test would notice, because the equality is invisible to the test layer. So
    we pin it both ways: the symbol must not be reintroduced, and the function must
    agree with the public set on every member and on a few non-members.
    """
    import inspect

    from graphxr_database_proxy.drivers.relational import compile as compile_module
    from graphxr_database_proxy.drivers.relational import mapping_index

    compile_source = inspect.getsource(compile_module)
    assert "_NO_TYPE_MARKERS" not in compile_source, (
        "compile.py must not redefine NO_TYPE; use mapping_index.NO_TYPE"
    )

    for marker in mapping_index.NO_TYPE:
        assert compile_module._is_no_type_marker(marker), (
            "compile._is_no_type_marker disagrees with mapping_index.NO_TYPE on %r" % (marker,)
        )
    for non_marker in ("INTEGER", "VARCHAR(50)", "BIGINT", "TEXT", "NUMERIC"):
        assert not compile_module._is_no_type_marker(non_marker), (
            "compile._is_no_type_marker should not recognise %r" % (non_marker,)
        )
