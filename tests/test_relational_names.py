"""
The two kinds of name a mapping holds, and which of them this proxy may reshape.

A mapping carries graph-side names — a label, a relationship type — and database-side
ones: the table, the key column, an endpoint's column. Graph-side names become Cypher and
half of a ``<Label>:<key>`` id, so they stay letters, digits and underscore. Database-side
names are the *engine's own spelling*: an ERP holding ``Order Items`` and a column called
``first name`` cannot be renamed to suit a regex, and every one of those names reaches SQL
through SQLAlchemy, which quotes it per dialect.

Discovery proposes the engine's spelling, so a loader that refused it was refusing its own
proposal: such a project could be discovered and could not be mapped, and the override a
person would have to hand-write to fix it was refused too. What is still refused is a name
carrying a control character, which no quoted identifier can hold on any of the three
engines.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from sqlalchemy.dialects import mssql, mysql, postgresql

from graphxr_database_proxy.drivers.relational import compile as compile_module
from graphxr_database_proxy.drivers.relational import plan, query
from graphxr_database_proxy.drivers.relational.catalog import propose
from graphxr_database_proxy.drivers.relational.engine import ServerInfo
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from graphxr_database_proxy.drivers.relational.mapping import SAFE_IDENT, from_document
from graphxr_database_proxy.drivers.relational.schemas import Endpoint, NodeSchema, Pair, RelSchema
from tests.test_relational_mapping import document, org_node, pair, person_node, works_for
from tests.test_relational_reflect import FakeInspector, table

#: ``Order Items`` with a column called ``first name``, and the child table that points
#: at it: the spellings a real order-entry schema arrives with, not a test's invention.
SPACED: Dict[str, Any] = {
    "Order Items": table(
        [("item id", "int", False), ("first name", "varchar(60)", True)],
        ("PK_Order Items", ["item id"]),
    ),
    "Order Lines": table(
        [("line id", "int", False), ("item id", "int", False), ("order id", "int", False)],
        ("PK_Order Lines", ["line id"]),
        [("FK_Line_Items", ["item id"], "Order Items")],
    ),
}


# ------------------------------------------------------------------ the round trip


def test_discovery_proposes_the_engines_spelling_and_the_loader_loads_it() -> None:
    """
    The draft ``propose`` writes for a spaced schema is a document ``from_document`` takes.

    Both names are asserted byte-for-byte: a mapping that loaded ``Order_Items`` instead of
    ``Order Items`` would be valid here and wrong at the server, which is the failure this
    whole change exists to make impossible.
    """
    draft = propose(FakeInspector(SPACED))

    out = from_document(draft, draft)

    assert out.problems == []
    assert out.valid
    node = out.nodes["Order_Items"]
    assert node.table == "Order Items"
    assert node.key == "item id"
    assert "first name" in node.props
    edge = out.edges["FK_LINE_ITEMS"]
    assert edge.table == "Order Lines"
    assert edge.key == "line id"
    assert edge.endpoints[0].dst.column == "item id"


def test_discovery_keeps_a_digit_led_table_and_invents_a_legal_label_for_it() -> None:
    """
    The two kinds, produced automatically rather than by hand.

    A table called ``2person`` is the engine's spelling and stays exactly that; the label
    is a name this proxy invents, so ``catalog.sanitize_identifier`` prefixes it (``_2person``)
    and the loader accepts the pair. Before the fix the pair could not be loaded at all,
    which is what made discovery's own draft refuse itself.
    """
    spec = {
        "2person": table(
            [("person id", "int", False), ("name", "varchar(60)", True)],
            ("PK_2person", ["person id"]),
        )
    }
    draft = propose(FakeInspector(spec))

    proposed = draft.model_dump(by_alias=True)["nodes"][0]
    assert proposed["table"] == "2person"
    assert proposed["label"] == "_2person"
    assert SAFE_IDENT.match(proposed["label"])

    out = from_document(draft, draft)
    assert out.problems == [], out.problems
    assert out.nodes["_2person"].table == "2person"
    assert out.nodes["_2person"].key == "person id"


def test_a_mapping_written_against_a_schema_nobody_can_reach_keeps_its_spaced_names() -> None:
    """No catalog, same answer: the file's names are the engine's, and nothing rewrites them."""
    doc = document(
        nodes=[person_node(table="Order Items", key="first name"), org_node()],
        edges=[
            works_for(
                table="Order Lines",
                endpoints=[pair("Person", "Organization", src_col="item id", dst_col="order id")],
            )
        ],
    )

    out = from_document(doc, None)

    assert out.problems == [], out.problems
    assert out.nodes["Person"].table == "Order Items"
    assert out.nodes["Person"].key == "first name"
    assert out.edges["WORKS_FOR"].table == "Order Lines"
    assert out.edges["WORKS_FOR"].endpoints[0].src.column == "item id"


def test_a_spaced_label_is_still_refused_where_a_spaced_table_is_not() -> None:
    """
    The distinction, on one document: the graph-side name is the thing that stays strict.

    ``Order Items`` as a table is a schema's spelling; as a label it is a Cypher token and
    half of an id, and a space there would need a second quoting rule to buy nothing.
    """
    accepted = from_document(
        document(nodes=[person_node(table="Order Items")]), None
    )
    assert accepted.valid, accepted.problems

    refused = from_document(document(nodes=[person_node(label="Order Items")]), None)

    assert [path for path, _ in refused.problems] == ["nodes[0].label"]
    assert "letters, digits and underscore" in refused.problems[0][1]


def test_an_edge_type_is_held_to_the_graph_rule_like_a_label() -> None:
    out = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for(type="Has Lines")]), None
    )
    assert [path for path, _ in out.problems] == ["edges[0].type"]
    assert "Has Lines" in out.problems[0][1]


# --------------------------------------------------------------------- what is refused


def test_a_name_holding_a_control_character_is_refused_and_says_why() -> None:
    """
    Not a spelling, a broken document: no engine carries a newline in an identifier.

    This is the only thing the database-side check refuses, so it has to name the character
    class rather than leave a person hunting for a typo in a name that looks fine.
    """
    node = from_document(document(nodes=[person_node(table="Order\nItems")]), None)
    assert [path for path, _ in node.problems] == ["nodes[0].table"]
    assert "control character" in node.problems[0][1]

    column = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Organization", src_col="item\x00id")])],
        ),
        None,
    )
    assert [path for path, _ in column.problems] == ["edges[0].endpoints[0].src.column"]
    assert "control character" in column.problems[0][1]

    key = from_document(document(nodes=[person_node(key="item\tid")]), None)
    assert [path for path, _ in key.problems] == ["nodes[0].key"]
    assert "control character" in key.problems[0][1]


# ---------------------------------------------------------------------- the SQL it makes

#: One engine and the dialect its SQL is rendered with, as ``test_relational_pipeline``
#: does it: the spelling decides the quoting, the dialect writes the text, no DBAPI needed.
ENGINES = {
    "mssql": (MSSQLEngine(), mssql.dialect(), ServerInfo(engine="mssql", major=15, minor=0, compat_level=150)),
    "postgresql": (PostgreSQLEngine(), postgresql.dialect(), ServerInfo(engine="postgresql", major=16, minor=4)),
    "mysql": (MySQLEngine(), mysql.dialect(), ServerInfo(engine="mysql", major=8, minor=0, build=4)),
}

NODES = {
    "LineItem": NodeSchema(
        label="LineItem",
        table="Order Items",
        key="item id",
        key_type="varchar(60)",
        props={"item id": "varchar(60)", "first name": "varchar(60)"},
    ),
    "Order": NodeSchema(
        label="Order",
        table="Order Header",
        key="order id",
        key_type="int",
        props={"order id": "int", "placed on": "date"},
    ),
}
EDGES = {
    "ON": RelSchema(
        type="ON",
        table="Order Lines",
        key="line id",
        key_type="int",
        endpoints=[
            Pair(
                src=Endpoint("LineItem", "item id", "varchar(60)"),
                dst=Endpoint("Order", "order id", "int"),
                row_estimate=2,
            )
        ],
        props={"line id": "int"},
    )
}

GRAPH_STATEMENT = "MATCH (l:LineItem)-[r:ON]->(o:Order) RETURN l, r, o"
SEEDED_STATEMENT = "MATCH (l:LineItem) WHERE l.`first name` = 'Avery' RETURN l"


def sql(statement: str, name: str) -> str:
    spelling, dialect, server = ENGINES[name]
    prepared = query.prepare(statement, default_limit=0)
    branch = plan.concrete_plans(prepared.plan, NODES, EDGES)[0]
    compiled = compile_module.compile_statement(
        branch, NODES, EDGES, spelling=spelling, server=server, spec=prepared.spec
    )
    return str(compiled.select.compile(dialect=dialect)).replace("\n", " ")


@pytest.mark.parametrize(
    "name, quoted_table, quoted_column",
    [
        ("mssql", "[Order Items]", "[item id]"),
        ("postgresql", '"Order Items"', '"item id"'),
        ("mysql", "`Order Items`", "`item id`"),
    ],
)
def test_a_spaced_table_and_column_reach_sql_quoted_the_way_each_engine_quotes(
    name: str, quoted_table: str, quoted_column: str
) -> None:
    """
    The engine's spelling survives to the statement, in that engine's own quotes.

    Nothing here hand-quotes: the point of the test is that the name arrives unharmed and
    SQLAlchemy is the one that wrapped it, which is what makes dropping the loader's regex
    safe rather than hopeful.
    """
    rendered = sql(GRAPH_STATEMENT, name)

    assert quoted_table in rendered
    assert quoted_column in rendered
    assert "Order Items" in rendered and "Order_Items" not in rendered


@pytest.mark.parametrize(
    "name, quoted_property",
    [("mssql", "[first name]"), ("postgresql", '"first name"'), ("mysql", "`first name`")],
)
def test_a_seed_predicate_on_a_spaced_property_compiles_quoted(name: str, quoted_property: str) -> None:
    """
    The other half of the path an ``/expand`` walks: the predicate is Cypher this proxy
    parses itself, and the property it names is still the column the engine has.

    ``test_relational_key_quoting`` pins the statement ``dialect.py`` writes for a spaced
    key; this pins that the parsed property is then bound to the real column, not to a
    guessed one.
    """
    rendered = sql(SEEDED_STATEMENT, name)

    assert quoted_property in rendered
    assert "first_name" not in rendered
