"""
The round-trip contract: ``to_yaml(doc) -> parse(text)`` is the same document.

The mapping document is the unit of save/restore. A ``PUT /api/relational/{project}/mapping``
takes a YAML string or a model, the route does ``parse(text)`` or ``parse(model)``, and the
``GET`` hands back both. If the round trip loses anything, a person who saves and reloads
sees a different document - silent corruption.

Four pins this file adds beside the ones in ``test_relational_mapping_io``:

1. ``to_yaml(doc) -> parse(text)`` returns the same ``GraphMapping`` for a hand-built
   document with six shapes (a minimal node, a node with ``properties``, a node with
   ``include: false``, a node with ``notes``, an edge with endpoints, an edge with
   ``flip: true``).
2. ``parse(doc)`` and ``parse(to_yaml(doc))`` converge: a model and the text the
   serializer wrote both load to the same document.
3. Unparseable input raises ``MappingFileError`` whose ``problems`` are
   ``(path, message)`` pairs - the shape the API converts to a 422.
4. ``parse`` is its own boundary: it does not know what an engine is. A mapping whose
   property declares a type the engine has no binding for (``String(50)``) parses
   cleanly - the route is what compiles to the engine and is the layer that refuses it.
"""

from __future__ import annotations

import yaml

import pytest

from graphxr_database_proxy.drivers.relational.mapping_io import (
    FILE_PATH,
    MappingFileError,
    parse,
    to_yaml,
)
from graphxr_database_proxy.models.graph_mapping import (
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
    MappingPair,
)


def _shape_doc(shape: str) -> GraphMapping:
    """A minimal document exercising one shape the round trip has to carry."""
    if shape == "minimal_node":
        return GraphMapping(
            nodes=[MappingNode(label="Person", table="Person", key="person_id")],
        )
    if shape == "node_with_properties":
        return GraphMapping(
            nodes=[MappingNode(
                label="Organization",
                table="Organization",
                key="org_id",
                properties={"org_id": "bigint", "legal_name": "varchar(80)"},
            )],
        )
    if shape == "node_with_include_false":
        return GraphMapping(
            nodes=[MappingNode(
                label="AuditLog",
                table="AuditLog",
                key="audit_id",
                include=False,
                notes="not a node: kept as a record of a decision",
            )],
        )
    if shape == "node_with_notes":
        return GraphMapping(
            nodes=[MappingNode(
                label="NoteTaker",
                table="Notes",
                key="note_id",
                notes="a node whose name is a verb and whose table is plural",
            )],
        )
    if shape == "edge_with_endpoints":
        return GraphMapping(
            nodes=[
                MappingNode(label="Person", table="Person", key="person_id"),
                MappingNode(label="Organization", table="Organization", key="org_id"),
            ],
            edges=[MappingEdge(
                type="WORKS_FOR",
                table="Employment",
                key="employment_id",
                endpoints=[MappingPair(
                    src=MappingEndpoint(label="Person", column="person_id"),
                    dst=MappingEndpoint(label="Organization", column="organization_id"),
                )],
            )],
        )
    # edge_with_flip_true
    return GraphMapping(
        nodes=[MappingNode(label="Person", table="Person", key="person_id")],
        edges=[MappingEdge(
            type="MENTORS",
            table="Mentor",
            key="mentor_id",
            flip=True,
            endpoints=[MappingPair(
                src=MappingEndpoint(label="Person", column="mentor_id"),
                dst=MappingEndpoint(label="Person", column="mentee_id"),
            )],
        )],
    )


SHAPES = [
    "minimal_node",
    "node_with_properties",
    "node_with_include_false",
    "node_with_notes",
    "edge_with_endpoints",
    "edge_with_flip_true",
]


@pytest.mark.parametrize("shape", SHAPES)
def test_to_yaml_then_parse_returns_the_same_document(shape: str) -> None:
    """
    The round trip in one line: ``to_yaml(doc) -> parse(text)`` is the same document.

    Each parametrised shape builds a minimal document exercising one facet, so a
    silent loss in the serializer points at the field that disappeared rather
    than at a diff a reader has to interpret.
    """
    doc = _shape_doc(shape)
    again = parse(to_yaml(doc))
    assert again == doc


def test_parse_model_and_parse_text_converge() -> None:
    """
    The two parse paths agree: parsing the YAML ``to_yaml`` wrote and parsing
    the dict the route's model-half writes both yield the same document.

    The route takes ``{"yaml": ...}`` straight to ``parse(text)`` and
    ``{"model": ...}`` via ``parse(yaml.safe_dump(body.model, sort_keys=False))``
    — two dyers, two text bodies, one ``parse``. Convergence means the editor
    saves the same document whichever half it sent it through.
    """
    doc = GraphMapping(
        schema_name="hr",
        nodes=[
            MappingNode(label="Person", table="Person", key="person_id"),
            MappingNode(
                label="Organization",
                table="Organization",
                key="org_id",
                properties={"org_id": "bigint", "legal_name": "varchar(80)"},
            ),
            MappingNode(
                label="AuditLog",
                table="AuditLog",
                key="audit_id",
                include=False,
                notes="not a node: kept as a record of a decision",
            ),
        ],
        edges=[
            MappingEdge(
                type="WORKS_FOR",
                table="Employment",
                key="employment_id",
                endpoints=[MappingPair(
                    src=MappingEndpoint(label="Person", column="person_id"),
                    dst=MappingEndpoint(label="Organization", column="organization_id"),
                )],
            ),
            MappingEdge(
                type="MENTORS",
                table="Mentor",
                key="mentor_id",
                flip=True,
                endpoints=[MappingPair(
                    src=MappingEndpoint(label="Person", column="mentor_id"),
                    dst=MappingEndpoint(label="Person", column="mentee_id"),
                )],
            ),
        ],
    )
    via_text = parse(to_yaml(doc))
    as_dict = doc.model_dump(mode="json", by_alias=True)
    via_model = parse(yaml.safe_dump(as_dict, sort_keys=False))
    assert via_text == via_model == doc


def test_unparseable_input_raises_mapping_file_error_with_path_and_message() -> None:
    """
    Bad input raises ``MappingFileError`` whose ``problems`` are
    ``(path, message)`` pairs - the shape the API converts to a 422.

    Three refusals, three shapes: a field of the wrong kind (a number where a
    label is), a typo the model does not know (an extra field), and a top
    level that is not a mapping at all.
    """
    # Field of the wrong kind - a number where a label is.
    with pytest.raises(MappingFileError) as caught:
        parse("version: 1\nnodes: [{label: 42, key: id}]\n")
    problems = caught.value.problems
    assert problems, "MappingFileError must carry at least one problem"
    assert all(isinstance(p, tuple) and len(p) == 2 for p in problems)
    paths = {path for path, _ in problems}
    assert "nodes[0].label" in paths or FILE_PATH in paths

    # Extra field - a typo the model does not know.
    with pytest.raises(MappingFileError) as caught:
        parse("version: 1\nexclue: [P]\n")
    assert caught.value.problems[0][0] == "exclue"
    assert "typo" in caught.value.problems[0][1]

    # Top level that is not a mapping - the file itself is the answer.
    with pytest.raises(MappingFileError) as caught:
        parse("- label: Person\n")
    assert caught.value.problems[0][0] == FILE_PATH


def test_parse_does_not_validate_engine_bindings() -> None:
    """
    ``parse`` is its own boundary: it does not know what an engine is.

    A mapping whose property declares a type the engine has no binding for
    (``String(50)`` - an engine binding the project does not have) loads
    cleanly here. The engine binding check is the route's job - the file is
    a description of a schema, the engine is the thing that compiles it,
    and ``parse`` returns the document without guessing which engine will
    compile it.

    If a future change made ``parse`` reject ``String(50)`` here, this test
    fails and forces the boundary to be declared: either ``parse`` learns
    about engines (and loses its single-responsibility claim), or the engine
    check moves up to the route, which is what the code does today.
    """
    doc = parse(
        "version: 1\n"
        "nodes: [{label: Person, table: Person, key: person_id,\n"
        "         properties: {name: String(50)}}]\n"
    )
    # The round trip is what would expose a parse path that refused on engine
    # grounds: parse twice, get the same document.
    again = parse(to_yaml(doc))
    assert again == doc
    # The ``String(50)`` string is preserved on the way out and the way back in:
    # parse stores it as a plain string, and to_yaml emits it as one.
    assert again.nodes[0].properties == {"name": "String(50)"}
