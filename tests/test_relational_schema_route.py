"""
``GET /api/{backend}/{project}/schema`` pinned at the route for the three relational engines.

This is the *relational* view of a project: table, column, and the type as the engine itself
spells it (``nvarchar(100)``, ``money``). ``/graphSchema`` normalises the same document into
the shared vocabulary and is pinned in ``tests/test_relational_driver.py``; the reflection
paging underneath both is pinned in ``tests/test_relational_pool.py`` and
``tests/test_relational_reflect.py``. What had no test at all was the answer a client
receives, and the three judgements it is made of: which tables the mapping puts on the list,
what a column whose declared type cannot be named is called, and what a mapping this proxy
refused to load does to a schema read.

That last one is the pair the sibling routes already answer: ``/graphSchema`` and
``/sampleData`` refuse an unusable mapping in the loader's own words. A ``/schema`` that kept
listing tables beside those two refusals would tell a person the project is readable while
nothing else about it was, so all three now say the same sentence — and the editor's own
``/preview`` deliberately does not, because it is the check a person runs *while* the document
is broken and its refusal names the pair they asked about.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from kineviz_database_proxy.drivers.relational.engine import PROPERTY_TYPES
from kineviz_database_proxy.drivers.relational.reads import VERDICTS, RelationalProblem
from kineviz_database_proxy.drivers.relational.schema_surface import UNKNOWN_TYPE
from tests.test_relational_capability_record import RELATIONAL_BACKENDS, relational_project
from tests.test_relational_pool import driver_for
from tests.test_relational_reflect import SPEC, FakeInspector, table as spec_table
from tests.test_relational_sample_data import answer, remapped, served, url

BACKENDS = pytest.mark.parametrize(
    "backend", RELATIONAL_BACKENDS, ids=lambda one: one.value
)

#: Every table the shared spec's draft uses: five records and two link tables. ``Order`` is
#: both — a direct foreign key is an edge whose table is the record table.
MAPPED_TABLES = {
    "Person", "Organization", "Order", "Session", "TreeNode", "Employment", "Manager"
}
#: The tables in the spec that are neither a record nor a link: no single-column key, so the
#: draft left them out and this read must not invent them.
UNMAPPED_TABLES = ("Label", "Audit", "Badge")

#: A mapping that loads and is then refused elsewhere: ``Audit`` keyed by two columns.
COMPOSITE = {
    "version": 1,
    "nodes": [
        {"label": "Person", "table": "Person", "key": "person_id", "include": True,
         "properties": {"full_name": "varchar(100)"}},
        {"label": "Audit", "table": "Audit", "key": "entity,when_id", "include": True},
    ],
    "edges": [],
}


@pytest.fixture
def route():
    """The three shipped relational projects, mapped from the shared spec."""
    with served([relational_project(one) for one in RELATIONAL_BACKENDS]) as (client, server):
        yield client, server


def schema(client: Any, backend: Any) -> Dict[str, Any]:
    return answer(client, backend, "/schema")


# ---------------------------------------------------------------------------
# the closed set of tables and the names they carry
# ---------------------------------------------------------------------------


@BACKENDS
def test_the_schema_names_the_tables_the_mapping_uses_and_only_those(route, backend):
    """
    The mapping's own table list, in the engine's own spelling, with nothing normalised.

    A record's table and a link's table are both here — a person reading the relational view
    wants the join columns too — and the three tables the draft could not key are absent, so
    the answer is the mapping, not the customer's library. The types are what discovery read
    (``varchar(100)``, ``money``) and not one of ``PROPERTY_TYPES``: normalising them here
    would leave ``/graphSchema`` with nothing to be the graph view of.
    """
    client, server = route
    tables = schema(client, backend)

    assert set(tables) == MAPPED_TABLES
    for name in UNMAPPED_TABLES:
        assert name not in tables, "{} is not part of this mapping".format(name)
    assert tables["Person"] == {"person_id": "int", "full_name": "varchar(100)"}
    assert tables["Order"]["total"] == "money"
    for column, declared in tables["Organization"].items():
        assert declared.upper() not in PROPERTY_TYPES, (column, declared)


@BACKENDS
def test_a_table_the_engine_does_not_have_is_absent_and_not_empty(route, backend):
    """
    A declared table the reflection cannot find leaves no entry, and is not a failure.

    An empty ``{}`` here reads as "a table with no columns", which is a different fact and one
    nobody would contradict; the reason for the gap is already a warning on the mapping the
    editor reads. What matters at this route is that one missing table does not turn the whole
    schema into an error a canvas cannot recover from.
    """
    narrow = {"Person": SPEC["Person"]}
    with served([relational_project(backend)], spec=narrow) as (client, server):
        tables = schema(client, backend)

    assert set(tables) == {"Person"}


# ---------------------------------------------------------------------------
# the paging the repo's law is about
# ---------------------------------------------------------------------------


@BACKENDS
def test_the_schema_reflects_a_page_of_names_at_a_time_and_never_walks_the_library(backend):
    """
    Thirty tables the mapping does not use sit in the same schema, and are never asked for.

    ``/schema`` bounds itself twice over: the names it hands the inspector are the mapping's
    own, and they go one ``names_page_size`` page at a time. Both halves are pinned here at the
    route, because a refactor that read ``get_table_names`` to filter the library would pass a
    test that only counted the answer.
    """
    wide = dict(SPEC)
    for index in range(30):
        wide["Extra{:03d}".format(index)] = spec_table([("id", "int", False)], (None, ["id"]))
    with served([relational_project(backend)], spec=wide) as (client, server):
        server.spelling_for(backend.value).names_page_size = 2
        tables = schema(client, backend)

    pages = server.reflected()
    assert pages, "the schema read reflected nothing"
    for page in pages:
        assert len(page) <= 2, page
        assert set(page) <= MAPPED_TABLES, page
    assert set(name for page in pages for name in page) == MAPPED_TABLES
    assert not any(call[0] == "tables" for call in server.inspector.calls), (
        "the read listed the customer's library to answer a mapping"
    )
    assert set(tables) == MAPPED_TABLES


# ---------------------------------------------------------------------------
# a type nobody can name
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_column_whose_declared_type_the_engine_cannot_name_is_still_a_column(backend):
    """
    The same sentinel ``/graphSchema`` falls back to, and never a dropped column.

    An inspector that reports no type (a denied catalog view, a domain a dialect does not
    know) is a gap in what the proxy was *told*, not a gap in the table. Refusing the read
    would cost the client every column of every table for one cell of one type; naming it the
    way ``schema_surface.UNKNOWN_TYPE`` does keeps the two views of the same column in
    agreement, which is what keeps ``render_key_literal`` quoting it as text.
    """
    spelled = dict(SPEC)
    spelled["Person"] = spec_table(
        [("person_id", "int", False), ("weird", None, True), ("blank", "   ", True)],
        ("PK_Person", ["person_id"]),
    )
    with served([relational_project(backend)], spec=spelled) as (client, server):
        tables = schema(client, backend)

    assert tables["Person"] == {
        "person_id": "int",
        "weird": UNKNOWN_TYPE,
        "blank": UNKNOWN_TYPE,
    }


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


@BACKENDS
def test_an_unusable_mapping_refuses_the_schema_in_the_words_the_other_reads_use(backend):
    """
    One broken field, one sentence, three routes that agree this project is not servable.

    ``Audit`` keyed by two columns loads as a document and is refused as a record: the graph
    contract carries one key pair per seed, so nothing can be expanded from it. ``/graphSchema``
    and ``/sampleData`` already answer that with the loader's own ``path: message``; a schema
    that kept listing tables beside those refusals is a second, quieter answer to the same
    question, and the tables a person needs in order to fix the document are one
    ``/api/relational/<project>/tables`` away.
    """
    broken = remapped(backend, COMPOSITE)
    with served([broken]) as (client, server):
        refused = client.get(url(backend, "/schema")).json()
        graph = client.get(url(backend, "/graphSchema")).json()
        rows = client.get(url(backend, "/sampleData")).json()

    assert refused["success"] is False
    assert refused["data"] is None, "a refused mapping still answered a table map"
    assert refused["error"] == graph["error"] == rows["error"], refused["error"]
    assert "nodes[1].key" in refused["error"] and "composite" in refused["error"]


@BACKENDS
def test_a_reflection_that_raises_is_the_engine_s_sentence_and_not_a_500(backend):
    """
    The catalog read is the one thing in this route that can fail on its own.

    Everything above it — a project that exists, a mapping that loads — already answered, so
    what a client gets has to be the engine's sentence and not an exception with no body: the
    routes have no wrapper, and a 500 here costs a person their schema panel and tells them
    nothing about the database behind it.
    """
    class Denied(FakeInspector):
        def get_multi_columns(self, tables: Any = None, schema: Any = None) -> Dict[str, Any]:
            raise RuntimeError("catalog access denied")

    with served([relational_project(backend)]) as (client, server):
        server.inspector = Denied(SPEC)
        body = client.get(url(backend, "/schema")).json()

    assert body["success"] is False
    assert body["error"] == "catalog access denied", body["error"]


# ---------------------------------------------------------------------------
# the asymmetry, said out loud: the editor's check answers the pair it was given
# ---------------------------------------------------------------------------


@BACKENDS
async def test_a_broken_mapping_still_answers_the_preview_of_a_pair_that_is_fine(backend):
    """
    ``/preview`` is the repair loop, so a document-level refusal would break it.

    The same document whose ``Audit`` record was refused for a composite key still previews an
    ``Employment`` link between two records that loaded, and a pair pointing at the refused
    record is refused by *that pair's* path — ``reads._resolve_end`` naming the endpoint that
    resolves to nothing. A gate on ``mapping.valid`` would answer both with
    ``nodes[1].key: composite`` and send the person fixing a field that was never the question.
    """
    wired = driver_for(remapped(backend, COMPOSITE), row_factory=_preview_rows)

    verdict = await wired.driver.preview(
        "Employment",
        {"src": {"label": "Person", "column": "person_id"},
         "dst": {"label": "Organization", "column": "org_id"}},
        edge="EMPLOYMENT",
    )

    assert verdict["verdict"] in VERDICTS, verdict["verdict"]
    assert verdict["forwards"]["rows"], "a preview of a pair that loaded answered nothing"
    with pytest.raises(RelationalProblem) as caught:
        await wired.driver.preview(
            "Audit",
            {"src": {"label": "Person", "column": "person_id"},
             "dst": {"label": "Audit", "column": "entity"}},
            edge="AUDITED",
        )
    assert caught.value.path == "Audit.dst", caught.value.path


def _preview_rows(statement: Any) -> List[Dict[str, Any]]:
    """One row per direction, keyed by the columns the preview projected."""
    return [{str(getattr(c, "name", "")): "value" for c in statement.selected_columns}]
