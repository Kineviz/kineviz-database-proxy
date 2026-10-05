"""
The mapping file: one parse path for YAML and JSON, one serializer for exports.

What a person actually gets wrong, answered by the control that is wrong rather than
by "invalid mapping": a label typo, a missing edge key, tabs in the indentation, a
file that is really a database dump, a JSON body, a top level that is a list.

And the promise the YAML tab depends on: two saves of one document are the same
bytes, so the text beside the checkboxes cannot flicker while a person is reading it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from graphxr_database_proxy.drivers.relational import mapping, mapping_io
from graphxr_database_proxy.drivers.relational.mapping_io import (
    EXAMPLE_PATH,
    FILE_PATH,
    MAX_MAPPING_BYTES,
    MappingFileError,
    parse,
    to_yaml,
)
from graphxr_database_proxy.models.graph_mapping import GraphMapping

SIMPLE = """
version: 1
schema: dbo
nodes:
  - label: Person
    table: Person
    key: person_id
    show_as: full_name
    notes: one row per living person
edges:
  - type: WORKS_FOR
    table: Employment
    key: employment_id
    endpoints:
      - src: {label: Person, column: person_id}
        dst: {label: Organization, column: organization_id}
  - type: REPORTS_TO
    table: PersonManager
    key: manager_id
    endpoints:
      - src: {label: Person, column: person_id}
        dst: {label: Person, column: manager_id}
"""


def problems_of(text: str) -> List[Any]:
    with pytest.raises(MappingFileError) as caught:
        parse(text)
    return caught.value.problems


# ------------------------------------------------------------------- one format


def test_yaml_and_json_are_the_same_file_read_twice() -> None:
    doc = parse(SIMPLE)
    as_json = json.dumps(doc.model_dump(mode="json", by_alias=True))
    assert parse(as_json) == doc


def test_a_json_body_is_not_a_special_case() -> None:
    doc = parse(json.dumps({"version": 1, "nodes": [{"label": "Person", "key": "id"}]}))
    assert doc.nodes[0].label == "Person"


def test_the_short_schema_key_and_the_attribute_name_are_both_accepted() -> None:
    short = parse("version: 1\nschema: sales\nnodes: [{label: Order, table: Order, schema: sales}]\n")
    assert short.schema_name == "sales"
    assert short.nodes[0].schema_name == "sales"
    spelled = parse("version: 1\nschema_name: sales\n")
    assert spelled.schema_name == "sales"
    # An export writes the short one, because that is what the example teaches.
    assert "schema: sales" in to_yaml(short)


def test_a_property_list_and_a_property_map_mean_the_same_thing() -> None:
    listed = parse("version: 1\nnodes: [{label: P, key: id, properties: [name, age]}]\n")
    typed = parse("version: 1\nnodes: [{label: P, key: id, properties: {name: STRING, age: INT32}}]\n")
    assert set(listed.nodes[0].properties) == {"name", "age"}
    assert listed.nodes[0].properties["name"] == "STRING"
    assert typed.nodes[0].properties["age"] == "INT32"


def test_a_field_that_is_not_in_the_format_is_refused_rather_than_ignored() -> None:
    caught = problems_of("version: 1\nexclue: [Person]\n")
    assert [path for path, _ in caught] == ["exclue"]
    assert "typo" in caught[0][1]


def test_the_document_version_is_checked_at_the_door() -> None:
    assert problems_of("version: 2\n")[0][0] == "version"
    assert parse("version: 1\n").version == 1


# --------------------------------------------------------------------- failures


def test_a_file_whose_top_level_is_a_list_names_the_file_not_a_field() -> None:
    caught = problems_of("- label: Person\n  key: id\n")
    assert caught[0][0] == FILE_PATH
    assert "mapping" in caught[0][1] and "list" in caught[0][1]


def test_an_empty_file_says_so_in_words_a_person_can_act_on() -> None:
    assert problems_of("# just a comment\n")[0][0] == FILE_PATH


def test_tabs_in_the_indentation_are_named_by_the_line_they_are_on() -> None:
    caught = problems_of("version: 1\nnodes:\n\t- label: Person\n")
    assert caught[0][0] == "line 3"
    assert "tab" in caught[0][1].lower()


def test_a_two_megabyte_file_is_refused_before_it_is_parsed() -> None:
    huge = "version: 1\n" + "# " + "x" * (MAX_MAPPING_BYTES + 1)
    caught = problems_of(huge)
    assert caught[0][0] == FILE_PATH
    assert "limit" in caught[0][1]
    # Just under the cap is not an error: the cap is a guard against a database dump,
    # not a size budget a real mapping has to fit inside.
    assert parse("version: 1\n# " + "x" * 100).version == 1


def test_a_label_typo_is_answered_by_the_loader_with_the_control_that_is_wrong() -> None:
    # The file itself is well-formed — `Persson` is a perfectly good string — so this is
    # a mapping mistake, and the loader is what says which control to light up.
    doc = parse(
        "version: 1\n"
        "nodes: [{label: Person, table: Person, key: id}]\n"
        "edges:\n"
        "  - type: WORKS_FOR\n    table: Employment\n    key: id\n"
        "    endpoints:\n"
        "      - src: {label: Persson, column: person_id}\n"
        "        dst: {label: Person, column: organization_id}\n"
    )
    out = mapping.from_document(doc, None)
    assert out.problems[0][0] == "edges[0].endpoints[0].src.label"


def test_a_missing_edge_key_is_named_at_the_key_control() -> None:
    doc = parse(
        "version: 1\n"
        "nodes: [{label: Person, table: Person, key: id}]\n"
        "edges:\n"
        "  - type: WORKS_FOR\n    table: Employment\n"
        "    endpoints:\n"
        "      - src: {label: Person, column: person_id}\n"
        "        dst: {label: Person, column: organization_id}\n"
    )
    out = mapping.from_document(doc, None)
    assert out.problems[0][0] == "edges[0].key"
    assert "parallel links" in out.problems[0][1]


def test_a_field_of_the_wrong_kind_is_found_by_path_not_by_count() -> None:
    caught = problems_of(
        "version: 1\nnodes: [{label: Person, key: id}]\n"
        "edges: [{type: W, key: id, endpoints: [{src: {label: Person, column: a}, "
        "dst: {label: 42, column: b}}]}]\n"
    )
    assert caught[0][0] == "edges[0].endpoints[0].dst.label"


def test_every_problem_of_a_broken_file_is_told_at_once() -> None:
    caught = problems_of(
        "version: 1\n"
        "nodes: [{table: Person, key: id}]\n"
        "edges: [{type: W, table: W, endpoints: [{src: {label: Person, column: a}}]}]\n"
    )
    assert [path for path, _ in caught] == ["nodes[0].label", "edges[0].endpoints[0].dst"]
    assert MappingFileError.at("nodes[0].key", "nope").problems == [("nodes[0].key", "nope")]
    assert "nodes[0].key: nope" in str(MappingFileError.at("nodes[0].key", "nope"))


def test_a_composite_key_written_as_a_list_is_refused_with_both_columns_named() -> None:
    # Flattening the list is what buys the message: "not a valid string" tells a
    # person nothing, while the loader can say the contract carries one key pair.
    doc = parse("version: 1\nnodes: [{label: P, key: [entity_id, when_id]}]\n")
    out = mapping.from_document(doc, None)
    assert out.problems[0][0] == "nodes[0].key"
    assert "entity_id" in out.problems[0][1] and "when_id" in out.problems[0][1]


# ------------------------------------------------------------------- determinism


def test_two_saves_of_one_document_are_the_same_bytes() -> None:
    doc = parse(SIMPLE)
    assert to_yaml(doc) == to_yaml(doc)
    assert to_yaml(parse(to_yaml(doc))) == to_yaml(doc)


def test_the_key_order_comes_from_the_format_and_not_from_the_file() -> None:
    written = parse(
        "nodes:\n"
        "  - key: person_id\n    table: Person\n    label: Person\n"
        "version: 1\n"
    )
    forwards = parse("version: 1\nnodes: [{label: Person, table: Person, key: person_id}]\n")
    assert to_yaml(written) == to_yaml(forwards)


def test_an_export_never_borrows_a_shape_it_can_share() -> None:
    doc = parse(SIMPLE)
    text = to_yaml(doc)
    # Two endpoints that read the same must not become an anchor and an alias: the file
    # is a document a person edits, and `*id12` is not editable.
    assert "&" not in text and "*id" not in text
    assert text.count("label: Person") >= 3


def test_an_export_writes_no_comments_and_carries_the_reasoning_as_fields() -> None:
    text = to_yaml(parse(SIMPLE))
    assert "#" not in text
    assert "one row per living person" in text
    assert "show_as: full_name" in text


def test_a_round_trip_loses_nothing_the_editor_needs() -> None:
    doc = parse(SIMPLE)
    again = parse(to_yaml(doc))
    assert again == doc
    assert again.nodes[0].notes == "one row per living person"
    assert again.edges[0].endpoints[0].dst.column == "organization_id"
    assert again.edges[1].endpoints[0].dst.column == "manager_id"


def test_defaults_are_written_out_rather_than_left_to_be_guessed() -> None:
    text = to_yaml(parse(SIMPLE))
    assert "flip: false" in text
    assert "include: true" in text
    # A count is the one default left out, because writing `0` for a pair nobody counted
    # would be the file claiming the engine said "empty". Absent *is* the answer, and it is
    # the same answer the field not being there gives on the way back in.
    assert "row_estimate" not in text
    # A null the person left empty is noise in a file they read, and means exactly what
    # the field not being there means.
    assert "null" not in text


def test_an_uncounted_pair_and_a_counted_empty_one_are_two_different_files() -> None:
    """
    The mapping document's half of the round trip: absent stays absent, zero stays zero.

    A serializer that turned one into the other would lose the distinction on the way to
    disk, and the next load would rank the pair on a number no engine ever gave.
    """
    uncounted = parse(SIMPLE)
    assert uncounted.edges[0].endpoints[0].row_estimate is None
    assert parse(to_yaml(uncounted)).edges[0].endpoints[0].row_estimate is None

    counted = parse(
        SIMPLE.replace(
            "      - src: {label: Person, column: person_id}\n"
            "        dst: {label: Organization, column: organization_id}\n",
            "      - src: {label: Person, column: person_id}\n"
            "        dst: {label: Organization, column: organization_id}\n"
            "        row_estimate: 0\n",
        )
    )
    assert counted.edges[0].endpoints[0].row_estimate == 0
    assert "row_estimate: 0" in to_yaml(counted)
    again = parse(to_yaml(counted))
    assert again == counted
    assert again.edges[1].endpoints[0].row_estimate is None


# ------------------------------------------------------------------- the example


def example_text() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def test_the_shipped_example_parses_and_validates_clean() -> None:
    doc = parse(example_text())
    assert doc.version == 1
    out = mapping.from_document(doc, None)
    assert out.problems == [], out.problems
    assert out.warnings == [], out.warnings
    assert sorted(out.nodes) == ["Order", "Organization", "Person", "Region"]
    assert sorted(out.edges) == ["MANAGES", "SHIPS_TO", "WORKS_FOR"]


def test_the_example_is_shipped_inside_the_package_not_looked_for_on_disk() -> None:
    assert EXAMPLE_PATH.name == "mapping.example.yml"
    assert EXAMPLE_PATH.parent.name == "data"
    # It sits next to the module that serves it, so an installed wheel carries it the
    # same way a source checkout does — and a packaging glob that forgets it is the one
    # way this 404s in production while passing here.
    assert EXAMPLE_PATH.is_relative_to(Path(mapping_io.__file__).parent)
    assert EXAMPLE_PATH.is_file()


def test_the_example_round_trips_through_the_model_without_losing_a_decision() -> None:
    doc = parse(example_text())
    again = parse(to_yaml(doc))
    assert again == doc
    # The re-export is what a Download hands back, so it has to load the same way too.
    assert mapping.from_document(again, None).valid


def test_the_example_teaches_the_one_shape_a_hand_written_file_gets_wrong() -> None:
    doc = parse(example_text())
    flipped = [edge for edge in doc.edges if edge.flip]
    assert flipped == []
    excluded = [node for node in doc.nodes if not node.include]
    assert [node.label for node in excluded] == ["AuditLog"]
    assert "keyed by" in (excluded[0].notes or "")
    assert doc.exclude == ["sysdiagrams"]
    assert doc.schema_name == "dbo"


def test_tuning_is_kept_as_the_free_map_it_is_declared_to_be() -> None:
    doc = parse("version: 1\ntuning: {auto_limit: 500, case_insensitive_like: auto}\n")
    assert doc.tuning == {"auto_limit": 500, "case_insensitive_like": "auto"}
    assert "auto_limit: 500" in to_yaml(doc)


def test_a_mapping_is_a_plain_dict_of_the_model_so_the_ui_can_read_it_directly() -> None:
    raw: Dict[str, Any] = parse(SIMPLE).model_dump(mode="json", by_alias=True)
    assert raw["nodes"][0]["show_as"] == "full_name"
    assert GraphMapping.model_validate(raw) == parse(SIMPLE)
