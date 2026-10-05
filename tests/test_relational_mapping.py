"""
Loading and checking a mapping document.

These are the invariants the visual editor promises, enforced here rather than in
TypeScript: excluding a record drops the links that needed it, a rename rewrites
every endpoint pointing at the old label, and every refusal is named by the path of
the control that is wrong. An uploaded file obeys them exactly as a clicked one does,
which is the reason they are tested against ``mapping.from_document`` and not against
a page.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from graphxr_database_proxy.drivers.relational.mapping import (
    COMPOSITE_KEY_REASON,
    PROVENANCE_ADDED,
    PROVENANCE_AUTO,
    PROVENANCE_EXCLUDED,
    PROVENANCE_FLIPPED,
    PROVENANCE_KINDS,
    PROVENANCE_RENAMED,
    EffectiveMapping,
    from_document,
)
from graphxr_database_proxy.models.graph_mapping import (
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
    MappingPair,
)


def endpoint(label: str, column: str, column_type: Optional[str] = None) -> MappingEndpoint:
    return MappingEndpoint(label=label, column=column, column_type=column_type)


def pair(src: str, dst: str, src_col: str = "from_id", dst_col: str = "to_id", rows: int = 0) -> MappingPair:
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


def test_every_provenance_a_page_can_show_is_one_of_the_five_it_documents() -> None:
    out = from_document(
        document(
            nodes=[person_node(label="Contact"), org_node()],
            edges=[works_for(endpoints=[pair("Contact", "Organization")]),
                   works_for(type="MANAGES", endpoints=[pair("Contact", "Contact")], flip=True)],
        ),
        catalog(),
    )
    assert out.valid, out.problems
    assert set(out.provenance.values()) <= set(PROVENANCE_KINDS)


# ----------------------------------------------------------------------- refusal


def test_an_identifier_the_engine_cannot_quote_is_refused_by_its_own_path() -> None:
    out = from_document(document(nodes=[person_node(label="Bad Name")]), None)
    assert ("nodes[0].label", ) and paths(out) == ["nodes[0].label"]
    assert "letters, digits and underscore" in messages(out)


def test_a_table_name_starting_with_a_digit_is_refused() -> None:
    out = from_document(document(nodes=[person_node(table="2person")]), None)
    assert paths(out) == ["nodes[0].table"]


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


def test_excluding_a_record_drops_the_links_that_needed_it_and_says_which() -> None:
    out = from_document(
        document(
            nodes=[person_node(include=False), org_node()],
            edges=[works_for()],
        ),
        catalog(),
    )
    # The file still loads: unticking a record is a decision, not a mistake. What has
    # to be visible is that a link went with it.
    assert out.valid, out.problems
    assert "WORKS_FOR" not in out.edges
    assert out.provenance["Person"] == PROVENANCE_EXCLUDED
    assert out.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED
    assert any("WORKS_FOR" in text and "Person" in text for text in out.warnings)


def test_the_include_list_is_a_whitelist_and_its_names_are_matched_both_ways() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for()],
        include=["Employment"],
    )
    out = from_document(doc, catalog())
    assert out.nodes == {}
    # `Employment` names a table, not a label, and still excluded the records that
    # were only ever reachable through it.
    assert out.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED


def test_an_exclude_naming_a_table_removes_the_record_by_that_name() -> None:
    out = from_document(document(nodes=[person_node(), org_node()], edges=[works_for()]),
                        catalog())
    assert out.valid
    dropped = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for()], exclude=["Person"]),
        catalog(),
    )
    assert "Person" not in dropped.nodes
    assert dropped.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED


def test_a_typo_in_a_scope_list_is_said_once_and_only_with_a_schema_in_front_of_us() -> None:
    with_catalog = from_document(
        document(nodes=[person_node()], exclude=["Persn"]), catalog()
    )
    assert any("Persn" in text for text in with_catalog.warnings)
    offline = from_document(document(nodes=[person_node()], exclude=["Persn"]), None)
    assert offline.warnings == []


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


# ---------------------------------------------------------------------- renames


def test_a_rename_rewrites_every_endpoint_pointing_at_the_old_label() -> None:
    doc = document(
        nodes=[person_node(label="Contact"), org_node()],
        edges=[works_for(endpoints=[pair("Person", "Organization")])],
    )
    out = from_document(doc, catalog())
    assert out.valid, out.problems
    assert "Person" not in out.nodes
    assert out.edges["WORKS_FOR"].endpoints[0].src.label == "Contact"
    assert out.provenance["Contact"] == PROVENANCE_RENAMED
    # The table never moved, so neither does the physical join: only the name GraphXR
    # sees changed, which is the entire point of a rename being safe to click.
    assert out.nodes["Contact"].table == "Person"


def test_a_rename_survives_being_written_by_hand_rather_than_clicked() -> None:
    doc = document(
        nodes=[person_node(label="Contact")],
        edges=[works_for(endpoints=[pair("Contact", "Organization")]),
               works_for(type="OLD_WAY", endpoints=[pair("Person", "Organization")])],
    )
    out = from_document(doc, catalog())
    assert out.valid, out.problems
    assert {p.src.label for p in out.edges["OLD_WAY"].endpoints} == {"Contact"}


def test_a_label_a_rename_left_behind_is_not_reported_as_a_typo() -> None:
    doc = document(nodes=[person_node(label="Contact"), org_node(label="Org")],
                   edges=[works_for(endpoints=[pair("Person", "Organization")])])
    out = from_document(doc, catalog())
    assert out.problems == []
    assert out.provenance["Contact"] == PROVENANCE_RENAMED
    assert out.provenance["Org"] == PROVENANCE_RENAMED


# ------------------------------------------------------------------- direction


def test_flipping_swaps_every_pair_of_the_link_and_marks_the_decision() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for(flip=True, endpoints=[pair("Person", "Organization", rows=7),
                                                pair("Person", "Organization", "alt_from", "alt_to", rows=3)])],
    )
    out = from_document(doc, catalog())
    endpoints = out.edges["WORKS_FOR"].endpoints
    assert {p.src.label for p in endpoints} == {"Organization"}
    assert out.provenance["WORKS_FOR"] == PROVENANCE_FLIPPED
    # The estimate went with the pair, so the representative is still the same link.
    assert out.edges["WORKS_FOR"].representative.row_estimate == 7


def test_the_representative_pair_and_the_row_count_come_from_the_document() -> None:
    doc = document(nodes=[person_node(), org_node()],
                   edges=[works_for(endpoints=[pair("Person", "Organization", rows=5),
                                                pair("Organization", "Person", rows=50)])])
    out = from_document(doc, None)
    assert out.edges["WORKS_FOR"].representative.src.label == "Organization"


# ----------------------------------------------------------------- catalog check


def test_a_table_that_moved_is_a_warning_and_never_a_blocked_save() -> None:
    out = from_document(document(nodes=[person_node(table="People")]), catalog())
    assert out.valid
    assert any("People" in text and "not in this schema" in text for text in out.warnings)


def test_a_column_that_no_longer_exists_is_a_warning_on_the_column() -> None:
    doc = document(nodes=[person_node(properties={"person_id": "int", "nickname": "varchar(9)"})])
    out = from_document(doc, catalog())
    assert out.valid
    assert any("nickname" in text for text in out.warnings)


def test_the_live_schema_wins_a_type_argument() -> None:
    doc = document(nodes=[person_node(properties={"person_id": "varchar(9)"})])
    out = from_document(doc, catalog())
    # A key bound as the wrong type is a full scan that raises nothing, so the schema
    # in front of us outvotes the file about a schema someone remembered.
    assert out.nodes["Person"].key_type == "int"
    assert out.nodes["Person"].all_props()["person_id"] == "int"


def test_endpoint_types_are_taken_from_the_edge_table_when_the_file_leaves_them_out() -> None:
    discovered = catalog()
    discovered.edges[0].endpoints[0].src.column_type = "int"
    out = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for()]), discovered
    )
    assert out.edges["WORKS_FOR"].endpoints[0].src.column_type == "int"


# ------------------------------------------------------------------- properties


def test_a_stated_property_list_is_a_whitelist_and_the_key_is_never_in_it_twice() -> None:
    out = from_document(document(nodes=[person_node()]), catalog())
    props = out.nodes["Person"].props
    assert "full_name" in props and "person_id" not in props
    assert list(out.nodes["Person"].all_props()) == ["person_id", "full_name"]


def test_excluding_a_property_takes_it_out_whatever_the_source_of_the_list_was() -> None:
    listed = from_document(
        document(nodes=[person_node(properties={"full_name": "varchar(100)", "photo": "varbinary(max)"}),
                        org_node()],),
        catalog(),
    )
    assert "photo" in listed.nodes["Person"].props
    dropped = from_document(
        document(nodes=[person_node(properties={"full_name": "varchar(100)", "photo": "varbinary(max)"},
                                     exclude_properties=["photo"]), org_node()]),
        catalog(),
    )
    assert "photo" not in dropped.nodes["Person"].props


def test_no_stated_properties_means_every_column_discovery_saw() -> None:
    out = from_document(document(nodes=[person_node(properties={})]), catalog())
    assert out.nodes["Person"].props == {"full_name": "varchar(100)"}


def test_an_edges_endpoint_columns_are_not_also_its_properties() -> None:
    edge = works_for(
        properties={"from_id": "int", "to_id": "bigint", "started_on": "date",
                    "employment_id": "int"}
    )
    out = from_document(document(nodes=[person_node(), org_node()], edges=[edge]), catalog())
    props = out.edges["WORKS_FOR"].props
    # The two columns that place the ends and the column that ids the link are all
    # structure; repeating them as relationship properties would double the panel and
    # let a person edit an endpoint by typing a property.
    assert props == {"started_on": "date"}


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


def test_the_duplicate_endpoint_pair_a_person_deletes_by_hand_is_not_two_links() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for(endpoints=[pair("Person", "Organization", rows=1),
                                    pair("Person", "Organization", rows=400)])],
    )
    out = from_document(doc, None)
    assert len(out.edges["WORKS_FOR"].endpoints) == 1
    assert out.edges["WORKS_FOR"].endpoints[0].row_estimate == 400
