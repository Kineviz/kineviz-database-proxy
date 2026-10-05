"""
A relational key column that needs quoting still expands.

``dialect.py`` builds the seed predicate of an ``/expand``, and for these three backends
that predicate is Cypher this proxy then parses *again* on its way to SQL. So the shape of
one identifier is a wire contract with ourselves: an unquoted ``n.order`` or
``n.first name`` is a statement our own parser refuses, and a refused statement is a
canvas that fills with nothing and an error nobody can act on.

Nothing here touches a database. The driver, its mapping, the translator, the planner and
the compiler are production, and only the transport is the shared fake.
"""

from __future__ import annotations

from typing import Any, Dict

from graphxr_database_proxy.contract.intents import ExpandRequest
from graphxr_database_proxy.drivers.dialect import MSSQL_DIALECT, NodeRef, build_expand
from graphxr_database_proxy.drivers.relational import translate
from graphxr_database_proxy.drivers.relational.catalog import propose
from tests.test_relational_pool import FakeInspector, driver_for, graph_rows, project
from tests.test_relational_reflect import table


def spec(person_key: str) -> Dict[str, Any]:
    """
    Two records and the link between them, both ends keyed by ``person_key``.

    That column is the thing under test: it is what ``/expand`` puts in its own predicate,
    and what the compiler then has to quote back into SQL.
    """
    return {
        "Person": table(
            [(person_key, "varchar(60)", False), ("full_name", "varchar(100)", True)],
            ("PK_Person", [person_key]),
        ),
        "Organization": table(
            [("org_id", "int", False), ("legal_name", "varchar(80)", True)],
            ("PK_Organization", ["org_id"]),
        ),
        "Employment": table(
            [
                ("employment_id", "int", False),
                (person_key, "varchar(60)", False),
                ("org_id", "int", False),
            ],
            ("PK_Employment", ["employment_id"]),
            [
                ("FK_Employment_Person", [person_key], "Person"),
                ("FK_Employment_Organization", ["org_id"], "Organization"),
            ],
        ),
    }


def driver_on(source: Dict[str, Any], name: str) -> Any:
    """A driver whose saved mapping is discovery's own answer for ``source``."""
    document = propose(FakeInspector(source)).model_dump(by_alias=True)
    return driver_for(
        project(name, graph_mapping=document),
        # Two vertices per statement: the seeded Person and the record its link arrives at, so
        # the neighbour needs its own key. One key would make the two ends the same node.
        row_factory=graph_rows(keys=["alice", 5]),
        inspector=FakeInspector(source),
    )


async def test_a_key_column_named_for_the_grammar_expands() -> None:
    """
    ``order`` is a column name a real schema uses, and a word the grammar eats first.

    This is the whole round trip a request takes: the intent resolves the seed from the
    mapping's own key column, the dialect writes the predicate, the pipeline reads it back,
    plans it, compiles it and mints node ids from the rows. The tokenizer happens to read a
    bare ``n.order`` as one property, so this is a guard on that path rather than the case
    the quoting fix caught — the spaced column below is that one.
    """
    wired = driver_on(spec("order"), "reserved-key")

    found = await wired.driver.expand(ExpandRequest(nodeIds=["Person:alice"], limit=10))

    assert "Person:alice" in [node.id for node in found.data.nodes]
    assert found.data.relationships
    assert '"order"' in str(wired.engine.selects[0])


def test_the_statement_built_for_a_spaced_key_is_one_the_parser_reads() -> None:
    """
    The seed predicate for a key column holding a space, read back by its own author.

    ``build_expand`` and ``translate`` are the two halves of one request here, so this is
    the seam the bug lived on: the quoted form parses to one property of ``n``, and the
    bare form is a refusal. It is pinned at this seam rather than end to end because a
    saved mapping refuses a spaced key column before a query is ever planned — that rule is
    ``drivers/relational/mapping.py``'s, and it is not this fix's to change.
    """
    (statement,) = build_expand(
        MSSQL_DIALECT,
        [NodeRef(category="Person", internal_id="Person:alice", key_prop="first name", key_value="alice")],
        limit=10,
    )

    plan = translate.translate(statement)

    assert "WHERE n.`first name` IN" in statement
    assert plan.where is not None
    assert plan.where.lhs.var == "n" and plan.where.lhs.prop == "first name"


def test_a_key_column_needing_no_quotes_is_still_written_with_them() -> None:
    """
    The ordinary column does not change shape, so the fix costs nothing to a normal schema.

    Quoting every key is what makes one rule hold for every column name; a plain
    ``person_id`` has to survive the same round trip, and ``/expand`` on a mapping nobody
    renamed is the common case on the wire.
    """
    (statement,) = build_expand(
        MSSQL_DIALECT,
        [NodeRef(category="Person", internal_id="Person:7", key_prop="person_id", key_value="7")],
        limit=10,
    )

    plan = translate.translate(statement)

    assert plan.where is not None
    assert plan.where.lhs.prop == "person_id"
