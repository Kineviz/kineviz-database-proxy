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

What a refusal then *does* is the other half of the rule: the record that needed the name is
not built, the reason stays the only message the person gets, and discovery does not answer
for a record the file was refused. One limit is documented rather than fixed — a column whose
own name holds a comma is read as two and refused, because escaping it is a file-format
decision.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from sqlalchemy.dialects import mssql, mysql, postgresql

from kineviz_database_proxy.drivers.relational import compile as compile_module
from kineviz_database_proxy.drivers.relational import plan, query
from kineviz_database_proxy.drivers.relational.catalog import propose
from kineviz_database_proxy.drivers.relational.engine import ServerInfo
from kineviz_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from kineviz_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from kineviz_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from kineviz_database_proxy.drivers.relational.mapping import (
    COMPOSITE_KEY_REASON,
    SAFE_IDENT,
    EffectiveMapping,
    from_document,
)
from kineviz_database_proxy.drivers.relational.schemas import Endpoint, NodeSchema, Pair, RelSchema
from tests.test_relational_mapping import (
    catalog,
    document,
    org_node,
    pair,
    person_node,
    works_for,
)
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


# --------------------------------------------------- a refusal stops the record


def no_empty_names(out: EffectiveMapping) -> None:
    """
    Nothing the loader handed back is keyed, joined or quoted on an empty name.

    A record carrying ``key == ""`` is the file's problem and the graph's success at once: its
    id would be ``Person:``, its direction preview would ask the engine for a column called
    ``""``, and the message that says why is the one nobody reads.
    """
    for node in out.nodes.values():
        assert node.label and node.table and node.key, node
        assert all(name for name in node.props), node
    for edge in out.edges.values():
        assert edge.type and edge.table, edge
        assert edge.key != "", edge
        for link in edge.endpoints:
            assert link.src.label and link.src.column, link
            assert link.dst.label and link.dst.column, link


def refused(out: EffectiveMapping, path: str, in_text: str) -> None:
    """Exactly one problem, at the control that is wrong, and nothing built on that name."""
    assert [where for where, _ in out.problems] == [path]
    assert in_text in out.problems[0][1], out.problems
    assert not out.valid
    no_empty_names(out)


def test_a_refused_key_leaves_the_record_out_instead_of_keying_it_on_nothing() -> None:
    """
    The two kinds of refusal agree: an unusable name answers ``None``, so the caller's guard
    is the guard the record actually needs.

    ``key: ""`` is the same case from the other side — a column was never named — and both
    end the same way: the label is not in the graph, and its field is named once.
    """
    control = from_document(document(nodes=[person_node(key="item\tid")]), None)
    assert "Person" not in control.nodes
    refused(control, "nodes[0].key", "node key")

    blank = from_document(document(nodes=[person_node(key="")]), None)
    assert "Person" not in blank.nodes
    refused(blank, "nodes[0].key", "single-column key")


def test_a_refused_key_is_the_message_and_nothing_else_is_said_about_it() -> None:
    """
    With a live catalog the leak was loudest: the reason, then ``check_column`` naming the
    empty string it had just been handed, then the columns of a record that never loaded.
    """
    out = from_document(document(nodes=[person_node(key="item\tid")]), catalog())
    assert "Person" not in out.nodes
    refused(out, "nodes[0].key", "control character")
    assert not any("''" in text for text in out.warnings), out.warnings
    assert not any("using" in text for text in out.warnings), out.warnings


def test_a_refused_record_is_not_answered_for_by_discovery() -> None:
    """
    The draft knows that table, that label and that type, and still puts none of them back.

    Discovery's copy carries its own table and key under the name the person's file was refused
    for, so the graph would hold a record the file never got — and a name it refuses a second
    time is a second problem at an ``auto.`` path no control in the document can highlight. The
    links that needed the record go with it, with the warning the endpoint already gives.
    """
    bad_label = from_document(document(nodes=[person_node(label="Bad Name")]), catalog())
    refused(bad_label, "nodes[0].label", "node label")
    assert "Person" not in bad_label.nodes and "Bad Name" not in bad_label.nodes
    assert any("did not load" in text for text in bad_label.warnings), bad_label.warnings

    bad_table = from_document(document(nodes=[person_node(table="Order\x00Items")]), catalog())
    refused(bad_table, "nodes[0].table", "control character")
    assert "Person" not in bad_table.nodes

    bad_edge_key = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for(key="id, tenant_id")]),
        catalog(),
    )
    refused(bad_edge_key, "edges[0].key", "composite")
    assert "WORKS_FOR" not in bad_edge_key.edges

    bad_draft_key = from_document(None, document(nodes=[person_node(key="id\x01")]))
    refused(bad_draft_key, "auto.nodes[Person].key", "control character")
    assert "Person" not in bad_draft_key.nodes


def test_silence_about_a_key_is_not_a_second_key() -> None:
    """
    A file that says nothing about its edge key, and a draft whose candidate is composite.

    The loader used to build the link keyless — the shape the document is allowed to mean —
    and then run the same draft again as ``auto.edges[...]`` and refuse it a second time. Two
    problems for one field, and a keyless link in the graph either way.
    """
    out = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for(key=None)]),
        document(nodes=[person_node(), org_node()], edges=[works_for(key="from_id, to_id")]),
    )
    assert [path for path, _ in out.problems] == ["edges[0].key"]
    assert "from_id" in out.problems[0][1] and "to_id" in out.problems[0][1]
    assert "WORKS_FOR" not in out.edges
    no_empty_names(out)


def test_a_refused_endpoint_takes_the_link_with_it() -> None:
    """
    An endpoint is the name a refusal reaches sideways: a pair needs both ends, so a column
    nobody can quote drops the link rather than joining against ``""``.
    """
    control = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Organization", src_col="item\x00id")])],
        ),
        None,
    )
    assert "WORKS_FOR" not in control.edges
    refused(control, "edges[0].endpoints[0].src.column", "control character")

    blank = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Organization", dst_col="")])],
        ),
        None,
    )
    assert "WORKS_FOR" not in blank.edges
    refused(blank, "edges[0].endpoints[0].dst.column", "no column name is declared")

    illegal_label = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Bad Name")])],
        ),
        None,
    )
    assert "WORKS_FOR" not in illegal_label.edges
    refused(illegal_label, "edges[0].endpoints[0].dst.label", "node label")


# --------------------------------------------------------- the comma in a name

#: A column a person really can be handed: an ERP that named its cost column after the unit
#: and the money, separator and all.
COMMA_COLUMN = "unit, cost"


def test_a_column_whose_name_holds_a_comma_is_read_as_two_and_refused() -> None:
    """
    The split is on ``,`` with no escaping rule, and that is a documented limit, not a bug.

    ``unit, cost`` arrives as a composite key, is refused naming both parts, and leaves the
    record unbuilt. Escaping a comma would change the file format — a contract decision above
    this seam — so the behaviour is pinned here and the limit is stated in the mapping guide,
    where the answer the refusal gives (name the surrogate) is the answer.
    """
    out = from_document(document(nodes=[person_node(key=COMMA_COLUMN)]), None)
    refused(out, "nodes[0].key", "composite")
    text = out.problems[0][1]
    assert "['unit', 'cost']" in text and COMPOSITE_KEY_REASON in text
    assert "Person" not in out.nodes


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
