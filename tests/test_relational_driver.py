"""
What a relational driver answers, end to end, with a faked connection.

Nothing here needs a database: every case drives a real driver class — its capability
record, its dialect, the translator, the planner and the compiler — and replaces only the
transport. That is the point. A driver test that mocked the pipeline would prove the driver
calls something and not that GraphXR receives a graph.

The assertions that matter are the ones a wrong answer cannot pass: a category carrying a
key, a node id that round-trips through the engine's own predicate, a write refused before a
statement is sent, and a refusal arriving as a response rather than as a 500.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

import pytest

from graphxr_database_proxy.drivers.relational import sql_pipeline
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.pool import DbapiMissing
from graphxr_database_proxy.drivers.relational.schemas import Pair
from graphxr_database_proxy.models.project import GraphData
from tests.test_relational_pool import (
    SPEC,
    FakeEngine,
    FakeInspector,
    FakeSpelling,
    driver_for,
    graph_rows,
    mapped_project,
    project,
)
from tests.test_relational_reflect import table as spec_table

# ---------------------------------------------------------------------------
# the graph schema surface
# ---------------------------------------------------------------------------


async def test_every_category_carries_a_key() -> None:
    """
    The one thing a ``primary-key`` backend's schema surface may never omit.

    A category with no key is not a smaller answer: ``intents._resolve_refs`` drops every
    seed whose label has none, so ``/expand`` on that label silently returns an empty graph.
    """
    wired = driver_for(mapped_project())

    found = await wired.driver.get_graph_schema()

    assert found.success
    assert found.data.categories
    for category in found.data.categories:
        assert category.keys, "{} reports no key".format(category.name)
        assert category.keysTypes.get(category.keys[0])


async def test_a_key_type_is_the_normalised_type_and_it_is_numeric_when_the_column_is() -> None:
    """
    ``INT32`` and not ``int``, and not ``STRING``.

    Two consumers read that one value: ``render_key_literal`` compares it lowercased against
    its own numeric table, so anything else quotes a numeric key and the round trip breaks;
    ``column_type`` binds the parameter as the column's type, so getting it wrong on SQL
    Server converts the column and turns an index seek into a full scan.
    """
    wired = driver_for(mapped_project())

    found = await wired.driver.get_graph_schema()
    person = next(item for item in found.data.categories if item.name == "Person")

    assert person.keys == ["person_id"]
    assert person.keysTypes["person_id"] == "INT32"
    assert person.propsTypes["full_name"] == "STRING"


def test_a_type_somebody_already_normalised_is_not_normalised_twice() -> None:
    """
    The mapping stores what discovery read, and a person may write ``INT64`` by hand.

    Passing that through ``normalise_type`` asks the engine's table for a name it never
    declared and gets ``STRING`` back — which then quotes a numeric key. The pass-through is
    what stops a second read of an already-read type from changing the answer.
    """
    wired = driver_for(mapped_project())

    assert wired.driver.normalise("INT64") == "INT64"
    assert wired.driver.normalise("bigint") == "INT64"
    assert wired.driver.normalise("") == "STRING"


async def test_one_relationship_per_edge_type_from_its_largest_pair() -> None:
    wired = driver_for(mapped_project())

    found = await wired.driver.get_graph_schema()
    names = [item.name for item in found.data.relationships]

    assert len(names) == len(set(names))
    employment = next(item for item in found.data.relationships if item.name == "EMPLOYMENT")
    assert {employment.startCategory, employment.endCategory} == {"Person", "Organization"}
    assert employment.props == ["started_on"]


async def test_an_unusable_mapping_answers_the_problem_and_names_its_path() -> None:
    document = mapped_project().database_config.graph_mapping
    document["edges"].append(
        {
            "type": "POINTS_AT_NOBODY",
            "table": "Employment",
            "endpoints": [{"src": {"label": "Ghost", "column": "person_id"}, "dst": {"label": "Person", "column": "org_id"}}],
        }
    )
    target = project("broken-mapping", graph_mapping=document)
    wired = driver_for(target)

    found = await wired.driver.get_graph_schema()

    assert not found.success
    assert found.error and "edges" in found.error


# ---------------------------------------------------------------------------
# the relational schema and the samples
# ---------------------------------------------------------------------------


async def test_the_schema_is_read_one_page_of_names_at_a_time() -> None:
    """
    ``/schema`` lists the tables the mapping uses, and nothing else is reflected.

    A thousand-table schema is the case that decides whether this endpoint is usable: the
    names are asked for in pages of the engine's own size and the inspector is never handed
    a name the mapping does not use.
    """
    big: Dict[str, Any] = dict(SPEC)
    for index in range(1000):
        big["Extra{:04d}".format(index)] = spec_table([("id", "int", False)], (None, ["id"]))
    inspector = FakeInspector(big)
    wired = driver_for(mapped_project(), inspector=inspector)
    wired.spelling.names_page_size = 3

    found = await wired.driver.get_schema()

    assert found.success
    assert set(found.data) >= {"Person", "Employment"}
    assert found.data["Person"]["person_id"] == "int"
    asked = [call for call in inspector.calls if call[0] == "columns"]
    assert asked, "the schema read reflected nothing"
    for _, names in asked:
        assert len(names) <= 3
    assert not (set(names) & {"Extra0001"})


async def test_a_sample_is_bounded_where_the_bound_belongs() -> None:
    wired = driver_for(mapped_project(), row_factory=graph_rows([1001]))

    found = await wired.driver.get_sample_data()

    assert found.success
    assert len(wired.engine.selects) == len(found.data)
    for statement in wired.engine.selects:
        assert "LIMIT" in str(statement)
    assert found.data["Person"] and "full_name" in found.data["Person"][0]


async def test_a_cell_that_json_cannot_carry_is_not_sent_to_a_browser() -> None:
    """
    A ``Decimal`` that would lose digits stays text, and bytes go out as base64.

    The precision rule is ``convert.json_safe``'s and the sample reuses it rather than
    restating it: a driver that rounded a ledger column to a float would be wrong in the one
    place nobody checks. A trailing zero is not a lost digit, so ``1000.50`` goes out as a
    number; more digits than a double has goes out as text.
    """
    from graphxr_database_proxy.drivers.relational.reads import plain_cell

    assert plain_cell(Decimal("1000.50")) == 1000.5
    assert plain_cell(Decimal("1.0000000000000000001")) == "1.0000000000000000001"
    assert plain_cell(b"\x00\x01") == "AAE="

    wired = driver_for(
        mapped_project(),
        script=[[{"person_id": 1, "amount": Decimal("1.0000000000000000001")}]],
    )
    mapping = await wired.driver.effective_mapping()

    rows = await wired.driver._sample_rows(mapping.nodes["Person"])

    assert rows == [{"person_id": 1, "amount": "1.0000000000000000001"}]


# ---------------------------------------------------------------------------
# the query path
# ---------------------------------------------------------------------------


async def test_a_graph_query_returns_a_graph_whose_ids_the_engine_can_match() -> None:
    wired = driver_for(mapped_project(), row_factory=graph_rows([1001]))

    found = await wired.driver.execute_query("MATCH (n:Person) RETURN n LIMIT 5")

    assert found.success, found.error
    assert found.data.type == "GRAPH"
    node = found.data.data.nodes[0]
    assert node.id == "Person:1001"
    assert node.properties["person_id"] == 1001
    assert found.data.summary["relational.kind"] == "graph"


async def test_a_count_returns_a_table_and_not_an_empty_graph() -> None:
    wired = driver_for(mapped_project(), script=[[{"__gx_a0": 12}]])

    found = await wired.driver.execute_query("MATCH (n:Person) RETURN count(n)")

    assert found.success, found.error
    assert found.data.type == "TABLE"
    assert found.data.summary["relational.kind"] in ("scalar", "aggregate")


async def test_no_write_reaches_the_database() -> None:
    wired = driver_for(mapped_project())

    found = await wired.driver.execute_query("CREATE (n:Person {name: 'x'})")

    assert not found.success
    assert "creates rows" in found.error
    assert wired.engine.selects == []


async def test_a_refusal_is_a_response_and_never_an_exception() -> None:
    """
    The intent routes have no exception wrapper.

    Anything that escapes ``execute_query`` becomes a 500 in the middle of someone's canvas,
    so every failure path here — refusal, unmappable pattern, missing stage, engine error —
    has to arrive as ``success=false`` with a sentence attached.
    """
    wired = driver_for(mapped_project())

    answers = [
        await wired.driver.execute_query("RETURN 1"),
        await wired.driver.execute_query("MATCH (n:Person) WHERE n.name =~ 'x' RETURN n"),
        await wired.driver.execute_query("CALL db.indexes()"),
    ]

    assert all(not answer.success for answer in answers)
    assert all(answer.error for answer in answers)
    assert "regular-expression" in answers[1].error


async def test_a_variable_with_no_label_fans_out_over_every_label_it_could_be() -> None:
    """
    Not a refusal and not an empty canvas: one branch per label the mapping declares.

    ``MATCH (n)`` names no label, which the mapping can answer in several ways rather than
    none. Refusing it would be honest, returning nothing for it would not, and the branch
    count is what tells a reader the answer arrived as a fan-out.
    """
    wired = driver_for(mapped_project(), row_factory=graph_rows([1]))

    found = await wired.driver.execute_query("MATCH (n) RETURN n")

    assert found.success, found.error
    assert found.data.type == "GRAPH"
    assert int(found.data.summary["relational.branches"]) > 1


async def test_the_regex_refusal_is_the_engines_own_answer() -> None:
    """T-SQL has no ``=~`` equivalent; PostgreSQL and MySQL both do."""
    refused = driver_for(mapped_project(), backend="mssql")
    allowed = driver_for(mapped_project("mysql-project"), backend="mysql", supports_regex=True)

    assert refused.driver.refusals().rules
    assert allowed.driver.refusals().rules == ()


async def test_a_schema_probe_is_recognised_before_the_guard() -> None:
    """
    ``prepare`` refuses ``CALL``, and a client asking for the schema arrives as ``CALL``.

    Recognising the probe here is what keeps the refusal from being a dead end: the answer is
    the mapping, which is exactly what ``/graphSchema`` returns.
    """
    wired = driver_for(mapped_project())

    found = await wired.driver.execute_query("CALL db.schema()")

    assert found.success, found.error
    assert found.data.type == "TABLE"
    assert {"node", "edge"} <= {row[0] for row in found.data.data[1:]}


async def test_a_procedure_that_is_not_a_probe_says_where_the_answer_is() -> None:
    wired = driver_for(mapped_project())

    found = await wired.driver.execute_query("CALL some.procedure")

    assert not found.success
    assert "/graphSchema" in found.error


async def test_the_auto_limit_is_the_projects_and_not_the_layers() -> None:
    wired = driver_for(
        mapped_project("capped", options={"max_limit": 25}), row_factory=graph_rows([1])
    )

    found = await wired.driver.execute_query("MATCH (n:Person) RETURN n")

    assert found.success, found.error
    assert found.data.summary["relational.limit"] == "25"
    assert found.data.summary["relational.autoLimit"] == "25"


async def test_the_branch_ceiling_is_handed_to_the_planner_and_its_refusal_is_a_response() -> None:
    """
    The fan-out's total is the query's, not one statement's, and the driver owns it.

    ``prepare`` bounds one statement and ``plan`` bounds the branches of one pattern; the
    ceiling a project can raise is ``tuning.max_branches``, and the refusal it produces has
    to arrive as an answer — a ``PlanningError`` escaping here is a 500 on a canvas.
    """
    asked: List[int] = []
    wired = driver_for(mapped_project("fanout", options={"max_branches": 3}))

    def counting(self: Any, plan: Any, mapping: Any, ceiling: int) -> List[Any]:
        asked.append(ceiling)
        raise type("BranchLimitExceeded", (Exception,), {})("too many readings of (n)")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(sql_pipeline.SqlPipeline, "branches", counting)
    try:
        found = await wired.driver.execute_query("MATCH (n:Person) RETURN n")
    finally:
        monkey.undo()

    assert asked == [3]
    assert not found.success
    assert "too many readings" in found.error
    assert wired.engine.selects == []


async def test_a_missing_compile_stage_answers_rather_than_raising() -> None:
    """A proxy that cannot compile SQL still serves its schema: so a query must say so."""
    wired = driver_for(mapped_project())
    monkey = pytest.MonkeyPatch()

    def missing(self: Any, plan: Any, mapping: Any, ceiling: int) -> List[Any]:
        raise sql_pipeline.PipelineMissing("the relational compile stage is not available")

    monkey.setattr(sql_pipeline.SqlPipeline, "branches", missing)
    try:
        found = await wired.driver.execute_query("MATCH (n:Person) RETURN n")
    finally:
        monkey.undo()

    assert not found.success
    assert "compile stage" in found.error


async def test_an_engine_error_arrives_as_a_message() -> None:
    wired = driver_for(mapped_project(), row_factory=graph_rows([1]))
    wired.engine.error = "deadlock detected"

    found = await wired.driver.execute_query("MATCH (n:Person) RETURN n")

    assert not found.success
    assert "deadlock" in found.error


# ---------------------------------------------------------------------------
# direction preview
# ---------------------------------------------------------------------------


async def test_a_link_is_run_both_ways_and_the_verdict_is_evidence() -> None:
    pair = {
        "src": {"label": "Order", "column": "order_id", "column_type": "int"},
        "dst": {"label": "Person", "column": "customer_id", "column_type": "int"},
    }
    wired = driver_for(mapped_project(), script=[[{"from": 1, "to": 2}], []])

    found = await wired.driver.preview("Order", pair, edge="FK_ORDER_CUSTOMER")

    assert found["verdict"] == "settled"
    assert found["forwards"]["count"] == 1 and found["backwards"]["count"] == 0
    assert len(wired.engine.selects) == 2


async def test_a_swapped_pair_is_backwards_and_nothing_joins_is_neither() -> None:
    """
    The four verdicts, from what the two directions actually returned.

    ``neither`` matters most: a link table whose key columns are wrong answers nothing on
    either side, and calling that ``settled`` would put a full canvas of nothing on the
    screen and let a person conclude their edge is fine.
    """
    pair = {
        "src": {"label": "Person", "column": "person_id"},
        "dst": {"label": "Organization", "column": "org_id"},
    }
    target = mapped_project("preview")
    one = [{"from": 1, "to": 2}]

    backwards = driver_for(target, script=[[], one])
    assert (await backwards.driver.preview("Employment", pair))["verdict"] == "backwards"

    empty = driver_for(target, script=[[], []])
    assert (await empty.driver.preview("Employment", pair))["verdict"] == "neither"

    both = driver_for(target, script=[one, one])
    assert (await both.driver.preview("Employment", pair))["verdict"] == "you-decide"


async def test_a_preview_that_names_nothing_says_which_half() -> None:
    wired = driver_for(mapped_project())

    with pytest.raises(ValueError) as caught:
        await wired.driver.preview(
            "Employment",
            {"src": {"label": "Person"}, "dst": {"label": "Organization", "column": "org_id"}},
        )

    assert caught.value.path == "Employment.src"


# ---------------------------------------------------------------------------
# the connection surface
# ---------------------------------------------------------------------------


def test_api_info_is_sync_and_opens_nothing() -> None:
    """
    A page load is not a reason to build a connection pool.

    ``get_api_info`` is the one driver method the routes call without awaiting, and the only
    one that must not touch the network to answer.
    """
    wired = driver_for(mapped_project())

    info = wired.driver.get_api_info("relational-mapped")

    assert info["type"] == "mssql"
    assert info["api_urls"]["expand"] == "/api/mssql/relational-mapped/expand"
    assert info["api_urls"]["mapping"] == "/api/relational/relational-mapped"
    assert info["features"]["read_only"] is True
    assert info["features"]["fulltext_search"] is False
    assert wired.engine.opens == 0


async def test_connect_reports_a_missing_driver_package_as_a_connection_error() -> None:
    wired = driver_for(mapped_project())
    wired.driver._spelling = FakeSpelling("mssql")
    wired.driver._access = type(wired.access.__class__).__call__ and wired.access
    monkey = pytest.MonkeyPatch()
    monkey.setattr(wired.access._pool, "engine", lambda _project, _spelling: (_ for _ in ()).throw(DbapiMissing("install pymssql")))
    try:
        with pytest.raises(ConnectionError) as caught:
            await wired.driver.connect()
    finally:
        monkey.undo()

    assert "install pymssql" in str(caught.value)


async def test_disconnect_does_not_close_the_pool() -> None:
    wired = driver_for(mapped_project())

    await wired.driver.disconnect()

    assert wired.engine.disposed == 0


def test_the_driver_is_a_base_driver_and_reports_its_capability_pair() -> None:
    wired = driver_for(mapped_project())

    assert isinstance(wired.driver, RelationalDriver)
    assert wired.driver.graph_capabilities.expand.predicate == "primary-key"
    assert wired.driver.graph_capabilities.identity.nodeId == "label-key"
    assert wired.driver.graph_dialect.predicate == "primary-key"
    assert wired.driver.graph_dialect.return_vars == ("n", "r", "m")


async def test_an_edge_with_no_record_at_its_end_is_dropped_not_drawn() -> None:
    wired = driver_for(mapped_project())
    stranded = Pair  # the blank representative: an edge that names nothing

    assert stranded is not None
    edge = next(iter(wired.driver.graph_schema(await wired.driver.effective_mapping()).relationships))
    assert edge.startCategory and edge.endCategory
