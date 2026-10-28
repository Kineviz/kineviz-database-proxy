"""
Loading and checking a mapping document.

These are the invariants the visual editor promises, enforced here rather than in
TypeScript: excluding a record drops the links that needed it, a rename rewrites
every endpoint pointing at the old label, and every refusal is named by the path of
the control that is wrong. An uploaded file obeys them exactly as a clicked one does,
which is the reason they are tested against ``mapping.from_document`` and not against
a page.

What is left here is the loader's own behaviour. The rules it delegates have a test
file each — ``test_relational_mapping_scope``, ``_index``, ``_props``, ``_pairs`` and
``_provenance`` — and those files import the builders below from this one.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from kineviz_database_proxy.drivers.relational.mapping import (
    COMPOSITE_KEY_REASON,
    PROVENANCE_ADDED,
    PROVENANCE_AUTO,
    PROVENANCE_EXCLUDED,
    EffectiveMapping,
    from_document,
)
from kineviz_database_proxy.models.graph_mapping import (
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
    MappingPair,
)


def endpoint(label: str, column: str, column_type: Optional[str] = None) -> MappingEndpoint:
    return MappingEndpoint(label=label, column=column, column_type=column_type)


def pair(
    src: str, dst: str, src_col: str = "from_id", dst_col: str = "to_id", rows: Optional[int] = 0
) -> MappingPair:
    """``rows=None`` is a pair no engine ever counted; ``rows=0`` is one it found empty."""
    return MappingPair(
        src=endpoint(src, src_col),
        dst=endpoint(dst, dst_col),
        row_estimate=rows,
    )


def document(
    nodes: Optional[List[MappingNode]] = None,
    edges: Optional[List[MappingEdge]] = None,
    **kwargs: object,
) -> GraphMapping:
    return GraphMapping(nodes=nodes or [], edges=edges or [], **kwargs)  # type: ignore[arg-type]


def person_node(**overrides: object) -> MappingNode:
    fields: Dict[str, object] = {
        "label": "Person",
        "table": "Person",
        "key": "person_id",
        "properties": {"person_id": "int", "full_name": "varchar(100)"},
    }
    fields.update(overrides)
    return MappingNode(**fields)  # type: ignore[arg-type]


def org_node(**overrides: object) -> MappingNode:
    fields: Dict[str, object] = {
        "label": "Organization",
        "table": "Organization",
        "key": "org_id",
        "properties": {"org_id": "bigint", "legal_name": "varchar(80)"},
    }
    fields.update(overrides)
    return MappingNode(**fields)  # type: ignore[arg-type]


def works_for(**overrides: object) -> MappingEdge:
    fields: Dict[str, object] = {
        "type": "WORKS_FOR",
        "table": "Employment",
        "key": "employment_id",
        "endpoints": [pair("Person", "Organization")],
    }
    fields.update(overrides)
    return MappingEdge(**fields)  # type: ignore[arg-type]


def catalog() -> GraphMapping:
    """What ``propose`` would have written for this same schema, types and all."""
    return document(
        nodes=[
            person_node(),
            org_node(),
            MappingNode(
                label="AuditLog",
                table="AuditLog",
                include=False,
                notes="not a node: no single-column primary key",
            ),
        ],
        edges=[works_for(properties={"started_on": "date"})],
    )


def paths(out: EffectiveMapping) -> List[str]:
    return [path for path, _ in out.problems]


def messages(out: EffectiveMapping) -> str:
    return " | ".join(text for _, text in out.problems)


# ------------------------------------------------------------------- shape only


def test_a_file_written_for_a_database_nobody_can_reach_still_loads() -> None:
    out = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for()]), None
    )
    assert out.valid, out.problems
    assert sorted(out.nodes) == ["Organization", "Person"]
    assert out.edges["WORKS_FOR"].endpoints[0].src.label == "Person"
    # The type the file stated is the type used, because there is nothing to correct it.
    assert out.nodes["Person"].key_type == "int"
    # And a column nobody typed is a string: quoted in the statement, never pasted bare.
    untyped = from_document(document(nodes=[person_node(properties={})]), None)
    assert untyped.nodes["Person"].key_type == "STRING"
    assert out.provenance["Person"] == PROVENANCE_ADDED


def test_the_document_is_the_whole_answer_when_discovery_never_ran() -> None:
    out = from_document(None, None)
    assert out.valid
    assert out.nodes == {} and out.edges == {}
    assert "no graph mapping" in out.warnings[0]


def test_no_override_at_all_is_just_discovery_read_straight() -> None:
    out = from_document(None, catalog())
    assert out.valid, out.problems
    assert out.provenance["Person"] == PROVENANCE_AUTO
    assert out.provenance["AuditLog"] == PROVENANCE_EXCLUDED
    assert "AuditLog" not in out.nodes
    assert out.nodes["Organization"].key_type == "bigint"


# ----------------------------------------------------------------------- refusal


def test_an_identifier_the_engine_cannot_quote_is_refused_by_its_own_path() -> None:
    out = from_document(document(nodes=[person_node(label="Bad Name")]), None)
    assert ("nodes[0].label", ) and paths(out) == ["nodes[0].label"]
    assert "letters, digits and underscore" in messages(out)


def test_a_table_name_starting_with_a_digit_is_the_engines_own_spelling() -> None:
    """
    A table name is the engine's, not ours to correct: ``2person`` is ordinary, and every
    one of the three engines quotes it — ``[2person]``, ``"2person"``, ``` `2person` ```.

    This case used to pin the opposite, when one regex served both kinds of name.
    """
    out = from_document(document(nodes=[person_node(table="2person")]), None)
    assert out.valid, out.problems
    assert out.nodes["Person"].table == "2person"


def test_a_label_starting_with_a_digit_is_still_refused() -> None:
    """The other half of the same name: a label becomes Cypher, where it has to stay an identifier."""
    out = from_document(document(nodes=[person_node(label="2person", table="2person")]), None)
    assert paths(out) == ["nodes[0].label"]
    assert "letters, digits and underscore" in messages(out)


def test_a_misspelled_endpoint_label_names_the_path_and_the_labels_that_exist() -> None:
    out = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Ogranization")])],
        ),
        None,
    )
    assert paths(out) == ["edges[0].endpoints[0].dst.label"]
    text = messages(out)
    assert "Ogranization" in text and "Person" in text and "Organization" in text
    # A typo here is not a warning: it is an edge type that silently never matches,
    # and the person finds that out a week later in an empty canvas.
    assert not out.valid


def test_one_name_used_for_both_a_node_and_an_edge_is_refused() -> None:
    out = from_document(
        document(
            nodes=[person_node(), org_node()],
            edges=[works_for(type="Person", endpoints=[pair("Person", "Organization")])],
        ),
        None,
    )
    assert paths(out) == ["edges[0].type"]
    assert "ambiguous" in messages(out)
    # The name means the record, and the link is the thing dropped: an id of
    # ``Person:42`` has to resolve to one of them.
    assert "Person" in out.nodes and "Person" not in out.edges


def test_a_node_needs_a_single_column_key_and_says_why() -> None:
    out = from_document(document(nodes=[person_node(key="a, b")]), None)
    assert paths(out) == ["nodes[0].key"]
    assert COMPOSITE_KEY_REASON in messages(out)
    assert "NodeRef" in messages(out)


def test_an_edge_written_a_key_the_contract_cannot_carry_is_refused_too() -> None:
    out = from_document(document(nodes=[person_node(), org_node()],
                                  edges=[works_for(key="from_id, to_id")]), None)
    assert paths(out) == ["edges[0].key"]


def test_an_edge_with_no_endpoint_pair_can_never_be_drawn() -> None:
    out = from_document(document(nodes=[person_node()], edges=[works_for(endpoints=[])]), None)
    assert paths(out) == ["edges[0].endpoints"]


def test_a_missing_key_is_a_refusal_in_a_file_and_a_fallback_with_a_catalog() -> None:
    edge = works_for(key=None)
    alone = from_document(document(nodes=[person_node(), org_node()], edges=[edge]), None)
    assert paths(alone) == ["edges[0].key"]
    assert "parallel links" in messages(alone)

    with_catalog = from_document(
        document(nodes=[person_node(), org_node()], edges=[edge]), catalog()
    )
    assert with_catalog.valid, with_catalog.problems
    assert with_catalog.edges["WORKS_FOR"].key == "employment_id"


def test_a_record_left_without_a_key_takes_discoverys_and_says_so() -> None:
    out = from_document(document(nodes=[person_node(key=None)]), catalog())
    assert out.valid, out.problems
    assert out.nodes["Person"].key == "person_id"
    assert any("nodes[0].key" in text for text in out.warnings)


def test_duplicates_are_named_by_the_second_entrys_path() -> None:
    out = from_document(
        document(
            nodes=[person_node(), person_node(label="Person2", table="Person"), org_node()],
            edges=[works_for(), works_for()],
        ),
        None,
    )
    assert paths(out) == ["nodes[1].table", "edges[1].type"]


# ------------------------------------------------------------------- cascades


def test_one_refused_record_is_reported_once_however_many_links_pointed_at_it() -> None:
    out = from_document(
        document(
            nodes=[person_node(key="a, b"), org_node()],
            edges=[works_for(endpoints=[pair("Person", "Organization")]),
                   works_for(type="KNOWS", endpoints=[pair("Person", "Organization")])],
        ),
        None,
    )
    assert paths(out) == ["nodes[0].key"]
    assert out.edges == {}
    # The links went with the record and said so. Six lit-up controls for the one field
    # a person has to fix is how a form stops being usable.
    assert any("did not load" in text for text in out.warnings)


# ------------------------------------------------------------------ passthrough


def test_the_reasoning_and_the_display_column_are_fields_and_stay_in_the_mapping() -> None:
    doc = document(
        nodes=[person_node(show_as="full_name", notes="one row per living person")],
        edges=[works_for(show_as="role", notes="one line per person per organization")],
    )
    out = from_document(doc, catalog())
    assert out.nodes["Person"].show_as == "full_name"
    assert out.nodes["Person"].notes == "one row per living person"
    assert out.edges["WORKS_FOR"].show_as == "role"
    assert out.edges["WORKS_FOR"].notes == "one line per person per organization"


def test_a_schema_named_by_the_document_reaches_every_record_it_declares() -> None:
    out = from_document(
        document(nodes=[person_node()], edges=[works_for()], schema_name="sales"),
        catalog(),
    )
    assert out.nodes["Person"].schema == "sales"
    assert out.edges["WORKS_FOR"].schema == "sales"
    specific = from_document(
        document(nodes=[person_node(schema_name="hr")], schema_name="sales"), catalog()
    )
    assert specific.nodes["Person"].schema == "hr"
