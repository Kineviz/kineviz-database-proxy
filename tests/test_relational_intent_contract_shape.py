"""
What the intent routes put on the wire, checked against the vendored contract rather than
against the models that happen to serialise it.

The routes answer through ``QueryResponse``, so a shape the models reject cannot arrive —
but nothing tied the models to ``contract.schema.json`` on the *response* side.
``tests/test_contract.py`` does exactly that for the capability record and the four request
bodies, and the same drift here goes the other way: a field renamed in one repo alone
(``startNodeId`` becoming ``start_node_id``) keeps validating and lands in the browser as an
edge with no ends. That is the gap these two cases close, over the real answers the routes
give: the same fake-driven projects ``tests/test_relational_graph_intents.py`` builds.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from graphxr_database_proxy.contract import load_contract_schema
from graphxr_database_proxy.models.project import GraphData, Node, RelationshipData
from tests.test_contract import declared_properties, model_fields
# ``client`` and ``server`` are the fixtures the routes-in-intent-mode file installs: the
# scripted projects, the credential override and the fake access every answer here runs
# through. ``server`` is not named by a case below, and is imported because resolving the
# ``client`` fixture from this module needs it in this module's namespace.
from tests.test_relational_graph_intents import PERSON, SEED, client, server, url


def contract_def(name: str) -> dict:
    definitions = load_contract_schema()["$defs"]
    assert name in definitions, "contract.schema.json has no definition for {}".format(name)
    return definitions[name]


def graph_result_schema() -> dict:
    """
    The GRAPH branch of ``GxrQueryResult``, which is a ``oneOf`` of the two result shapes.

    Picked by its own ``const`` rather than by position, so a branch added or reordered in
    the vendored file cannot make this check read the table shape by accident.
    """
    branches = [
        one
        for one in contract_def("GxrQueryResult")["oneOf"]
        if one["properties"]["type"]["const"] == "GRAPH"
    ]
    assert len(branches) == 1, contract_def("GxrQueryResult")
    return branches[0]


def matches_contract(one: Dict[str, Any], schema: dict) -> List[str]:
    """What is wrong with this wire object as the vendored definition reads it."""
    problems = ["missing {}".format(field) for field in schema["required"] if field not in one]
    declared = set(schema["properties"])
    problems += ["undocumented {}".format(key) for key in one if key not in declared]
    return problems


@pytest.mark.parametrize(
    "route, body",
    [
        ("/expand", {"nodeIds": [SEED], "limit": 10}),
        ("/pullCategory", {"category": "Person", "limit": 5}),
        ("/pullRelationship", {"relationship": "EMPLOYMENT", "limit": 5}),
    ],
    ids=["expand", "pullCategory", "pullRelationship"],
)
def test_every_intent_answer_carries_exactly_the_fields_the_contract_declares(
    client, route, body
):
    """Every object in the answer, required keys present and no invented ones."""
    answer = client.post(url(PERSON, route), json=body).json()
    data = answer["data"]["data"]

    assert matches_contract(answer["data"], graph_result_schema()) == []
    assert answer["data"]["type"] == "GRAPH"
    assert matches_contract(data, contract_def("GxrGraphData")) == []
    assert data["nodes"] or data["relationships"], "an empty answer would make this vacuous"
    for node in data["nodes"]:
        assert matches_contract(node, contract_def("GxrNode")) == []
    for relationship in data["relationships"]:
        assert matches_contract(relationship, contract_def("GxrRelationship")) == []


def test_the_models_that_answer_the_intents_match_the_definitions_they_are_serialised_from():
    """
    The drift check ``test_contract.py`` runs for the request bodies, for the response ones.

    These three models are what every intent route serialises through, so a field added to
    one of them without the vendored schema knowing is a field the client will never read —
    and a field the schema requires that the model dropped is a 200 with an unusable graph.
    """
    for model, definition in (
        (Node, "GxrNode"),
        (RelationshipData, "GxrRelationship"),
        (GraphData, "GxrGraphData"),
    ):
        assert model_fields(model) == declared_properties(definition), definition
