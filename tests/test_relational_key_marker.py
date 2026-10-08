"""
The key type marker story: where ``STRING`` and ``NULL`` both mean "nobody named
this column", and why the key path must agree with the property path about that.

The property path (``mapping_props``) already uses ``names_a_type`` to decide
"does the catalog have a type name for this column?". ``NULL`` answers no, so
the document's declaration survives: that is what ``bb0e72d`` made the rule.

The key path (``driver_mapping._key_unnamed``) used a narrower test that only
treated blank and ``STRING`` as "nobody looked", so a reflected ``NULL`` was
read as a declaration, the bounded catalog read was skipped, and that ``NULL``
became the key's declared type. ``compile._require_bindable_key`` then refused
every statement that reached the record. ``mapping.py:320`` reads the index
without consulting the key predicate, so a node keyed on a column that an edge
on the same table also names can reach ``key_type = "NULL"`` from the index
write alone.

These tests pin the two predicates on the same side of the line, and pin the
end-to-end route so the gap is closed rather than left in a report.
"""

from __future__ import annotations

from graphxr_database_proxy.drivers.relational.driver_mapping import _key_unnamed
from graphxr_database_proxy.drivers.relational.mapping import from_document
from graphxr_database_proxy.drivers.relational.mapping_index import names_a_type
from graphxr_database_proxy.models.graph_mapping import (
    UNKNOWN_TYPE,
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
    MappingPair,
)
from tests.test_relational_mapping import catalog, document, org_node, person_node, works_for


# -- the two predicates ----------------------------------------------------------------


def test_names_a_type_treats_every_no_type_value_as_no_name() -> None:
    """
    The property-path predicate, at its boundary.

    ``""``, ``STRING`` (``UNKNOWN_TYPE``) and ``NULL`` are all the same fact:
    nobody named this column. The property path already uses this test, so this
    is a re-pinning of a fact the rest of the file relies on.
    """
    for value in ("", UNKNOWN_TYPE, "NULL", None):
        assert not names_a_type(value), value
    for value in ("int", "bigint", "varchar(50)"):
        assert names_a_type(value), value


def test_key_unnamed_agrees_with_names_a_type_on_every_no_type_value() -> None:
    """
    The two predicates must read the same value the same way.

    One of them drives the property path's survival rule; the other decides
    whether ``with_key_types`` re-reads the live catalog. A drift here is a
    property type that the document corrects and a key type it cannot, for
    the same column, in the same load — which is the bug this file closes.
    """
    for value in ("", UNKNOWN_TYPE, "NULL", None, "int", "bigint", "varchar(50)"):
        # ``_key_unnamed`` accepts a string; normalise the ``None`` case to a blank.
        key_arg = "" if value is None else value
        assert _key_unnamed(key_arg) == (not names_a_type(value)), value


def test_key_unnamed_sees_null_as_nobody_looked() -> None:
    """
    A reflected ``NULL`` is a name the catalog did not write, not a declaration.

    All three engines (``postgresql._reflect_type``, ``mssql._parse_column_info``,
    ``mysql._parse_column``) emit ``NULL`` for a ``sqltypes.NULLTYPE`` after
    warning "Did not recognize type". Treating that as a declaration would hand
    a non-name to ``spelling.column_type``, which returns ``None`` for ``NULL``
    on every engine, and ``_require_bindable_key`` raises for the label on every
    statement that reaches it.
    """
    assert _key_unnamed("NULL")
    assert _key_unnamed(" null ")
    assert _key_unnamed("Null")
    assert _key_unnamed("NULL\t")


# -- the end-to-end route ----------------------------------------------------------------


def _both_record_on_same_table_doc() -> GraphMapping:
    """
    The schema that triggers the route: a node and an edge on the *same* table.

    ``mapping_index._CatalogIndex`` writes the edge endpoint's ``column_type``
    into ``columns[edge.table][endpoint.column]``. The node then reads that
    column map at ``mapping.py:320`` for its own key — which only collides when
    the node and the edge live on the same table. A self-referencing link is
    the natural case; any table that holds both a record and a foreign key
    pointing at itself is one.
    """
    return GraphMapping(
        nodes=[
            MappingNode(
                label="Employment",
                table="Employment",
                key="person_id",
            ),
        ],
        edges=[
            MappingEdge(
                type="COLLEAGUE_OF",
                table="Employment",
                endpoints=[
                    MappingPair(
                        src=MappingEndpoint(
                            label="Employment",
                            column="person_id",
                            column_type="NULL",
                        ),
                        dst=MappingEndpoint(
                            label="Employment",
                            column="person_id",
                            column_type="NULL",
                        ),
                    )
                ],
            )
        ],
    )


def test_a_node_keyed_on_a_column_an_edge_records_as_null_lands_key_type_null() -> None:
    """
    The route: ``mapping_index`` writes ``"NULL"`` for the edge endpoint column,
    then ``mapping.py:320`` reads it for the node on the same table.

    The brief reported this as reachable end to end; this is the test that
    makes the report a fact. The route does not need a live database to walk:
    a draft (the document used as its own catalog) is enough, because the
    index is built from whatever the loader is handed.
    """
    doc = _both_record_on_same_table_doc()
    catalog_doc = GraphMapping.model_validate(doc.model_dump(by_alias=True))

    out = from_document(doc, catalog_doc)

    assert out.valid, out.problems
    assert out.nodes["Employment"].key_type == "NULL"
    # The endpoint's column type the index wrote is the same value the node
    # reads: that is the whole route, captured as one assertion each.
    assert (
        out.edges["COLLEAGUE_OF"].endpoints[0].src.column_type == "NULL"
    )
    assert (
        out.edges["COLLEAGUE_OF"].endpoints[0].dst.column_type == "NULL"
    )


def test_the_route_is_neutral_to_whether_properties_are_stated() -> None:
    """
    The index write is the source of the ``"NULL"``; the document's property
    list is not in the picture until the fallback at ``mapping.py:320`` runs.

    Stating ``properties={...}`` does not deflect the index read because the
    index write happens first and a non-empty string is truthy. The second
    branch is included so the doc-driven override path is the one that
    *fails* to mask the bug, not just the path with nothing declared.
    """
    base = _both_record_on_same_table_doc()
    # Variant 1: nothing stated on the key column.
    bare = from_document(base, GraphMapping.model_validate(base.model_dump(by_alias=True)))
    assert bare.nodes["Employment"].key_type == "NULL"

    # Variant 2: a property type is stated for the key column.
    stated = MappingNode(label="Employment", table="Employment", key="person_id",
                          properties={"person_id": "int"})
    restated = GraphMapping(nodes=[stated], edges=list(base.edges))
    restated_catalog = GraphMapping.model_validate(restated.model_dump(by_alias=True))
    with_props = from_document(restated, restated_catalog)
    # ``mapping.py:320`` reads the index first; the index's ``"NULL"`` wins.
    assert with_props.nodes["Employment"].key_type == "NULL"


def test_the_property_path_already_treats_null_as_nobody_looked() -> None:
    """
    The mirror of the key route on the property path: a property column an
    edge records as ``"NULL"`` does not pick up that ``"NULL"`` — the
    property path's ``mapping_props`` consults ``names_a_type`` and falls
    through to the document's declaration. This is the contrast the key
    route is missing.

    A separate column from the key is used because ``mapping_props._props``
    drops the key from the property map (``drop={key}`` at the call site
    in ``mapping.py:319``); the property path only matters for non-key
    columns, and that is what this test exercises.
    """
    doc = GraphMapping(
        nodes=[
            MappingNode(
                label="Person",
                table="Person",
                key="person_id",
                properties={"person_id": "int", "manager_id": "int"},
            ),
            org_node(),
        ],
        edges=[
            MappingEdge(
                type="WORKS_FOR",
                table="Employment",
                endpoints=[
                    MappingPair(
                        src=MappingEndpoint(
                            label="Person",
                            column="manager_id",
                            column_type="NULL",
                        ),
                        dst=MappingEndpoint(
                            label="Organization",
                            column="org_id",
                            column_type="bigint",
                        ),
                    )
                ],
            )
        ],
    )
    out = from_document(doc, document(nodes=[person_node(), org_node()], edges=[works_for()]))
    # ``manager_id`` is a property column (not the key); the catalog recorded
    # it as ``"NULL"`` because the edge endpoint wrote it. The property path
    # reads ``names_a_type("NULL") == False``, so the document's "int" wins:
    # the property keeps a real type, not "NULL". This is the behaviour the
    # key path needs to mirror.
    assert out.nodes["Person"].props["manager_id"] == "int"


# -- the contract the loader enforces today ------------------------------------------------


def test_a_catalog_that_records_an_endpoint_as_null_writes_null_into_the_index() -> None:
    """
    The half the route sits on: a column the catalog marks as ``"NULL"`` is in
    ``_CatalogIndex.columns`` as ``"NULL"``. The other half — that ``mapping.py:320``
    then reads it — is the test above. Pinning the index write here closes the
    gap from the other side.
    """
    from graphxr_database_proxy.drivers.relational.mapping_index import _CatalogIndex

    doc = _both_record_on_same_table_doc()
    index = _CatalogIndex.of(doc)

    assert index.columns["Employment"]["person_id"] == "NULL"
    assert index.live


def test_a_node_keyed_on_a_different_column_does_not_see_the_null() -> None:
    """
    The route is local to the table the edge lives on. A node on a different
    table keyed on the same column *name* does not read the edge's index
    write — because the per-table column map is keyed by table, not by column
    name. This is the check that rules out "the route is broader than it
    looks".
    """
    doc = GraphMapping(
        nodes=[
            MappingNode(
                label="Person",
                table="Person",
                key="person_id",
                properties={"person_id": "int"},
            ),
            org_node(),
        ],
        edges=[
            MappingEdge(
                type="WORKS_FOR",
                table="Employment",
                endpoints=[
                    MappingPair(
                        src=MappingEndpoint(
                            label="Person",
                            column="person_id",
                            column_type="NULL",
                        ),
                        dst=MappingEndpoint(
                            label="Organization",
                            column="org_id",
                            column_type="bigint",
                        ),
                    )
                ],
            )
        ],
    )
    out = from_document(doc, catalog())

    # The node's table (``Person``) is not the edge's table (``Employment``),
    # so the index write on ``Employment`` is invisible to the node. The
    # ``properties`` declaration still wins.
    assert out.nodes["Person"].key_type == "int"
    assert out.edges["WORKS_FOR"].endpoints[0].src.column_type == "NULL"


# -- the honest re-read the alignment buys -------------------------------------------------


def test_declared_key_hands_back_null_when_the_catalog_also_could_not_name_it() -> None:
    """
    After the alignment, the bounded catalog read runs for ``"NULL"`` key types,
    and ``_declared_key`` is the line that decides whether the re-read buys a new
    outcome or just an honest path.

    The contract, pinned here in one test:

    * catalog also names it ``"NULL"`` — ``_declared_key`` returns ``"NULL"``,
      the key_type stays ``"NULL"``, ``compile._require_bindable_key`` still
      refuses. **The change buys an honest re-read, not a new outcome.**
    * catalog names it a real type — ``_declared_key`` returns the real type,
      the key_type is filled in, the refusal lifts.
    * column is absent from the catalog — ``_declared_key`` returns ``None``,
      the key_type stays whatever it was.

    All three live in one test because they are the same fact about one function:
    what the catalog read can answer, and what it cannot.
    """
    from types import SimpleNamespace

    from graphxr_database_proxy.drivers.relational.driver_mapping import _declared_key

    null_entry = SimpleNamespace(columns={"person_id": "NULL"})
    assert _declared_key({"Employment": null_entry}, "Employment", "person_id") == "NULL"

    named_entry = SimpleNamespace(columns={"person_id": "int"})
    assert _declared_key({"Employment": named_entry}, "Employment", "person_id") == "int"

    missing_entry = SimpleNamespace(columns={"other": "int"})
    assert _declared_key({"Employment": missing_entry}, "Employment", "person_id") is None

    # And the table itself being absent — the whole entry is None.
    assert _declared_key({}, "Employment", "person_id") is None


# -- the rule the key path must follow after the alignment --------------------------------


def test_key_unnamed_reads_the_canonical_no_type_set() -> None:
    """
    The key predicate sees every value ``names_a_type`` rejects.

    A pinned membership test: any new entry added to
    ``mapping_index.NO_TYPE`` must reach ``_key_unnamed`` too, or this
    fails. That is the contract that keeps the property path and the key
    path on the same side of the line.
    """
    from graphxr_database_proxy.drivers.relational.mapping_index import NO_TYPE

    for value in NO_TYPE:
        # ``None`` is not a member of a ``frozenset`` of strings; the
        # predicate accepts a string and treats ``None`` as blank.
        key_arg = "" if value is None else value
        assert _key_unnamed(key_arg), value
    # And a real name answers False on the key side.
    for value in ("int", "bigint", "varchar(50)"):
        assert not _key_unnamed(value), value
