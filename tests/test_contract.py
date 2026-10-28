"""
Keeps the proxy's contract models in step with the vendored
``contract.schema.json``, which is generated from ``shared/graphdb`` in
graphxr-dev.

graphxr-dev has the mirror test. Between them, a field added on one side and
forgotten on the other fails a test instead of becoming a runtime mismatch
between two repositories.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.contract import (
    CONTRACT_VERSION,
    CapabilityReport,
    ExpandRequest,
    FulltextSearchRequest,
    GraphDbCapabilities,
    PullCategoryRequest,
    PullRelationshipRequest,
    load_contract_schema,
)
from kineviz_database_proxy.contract.capabilities import (
    CapabilityError,
    assert_capabilities_consistent,
)


def schema_def(name: str) -> dict:
    definitions = load_contract_schema()["$defs"]
    assert name in definitions, f"contract.schema.json has no definition for {name}"
    return definitions[name]


def declared_properties(name: str) -> set:
    return set(schema_def(name).get("properties", {}).keys())


def model_fields(model) -> set:
    """Field names as they go on the wire, so aliases (``schema_``) compare right."""
    return {field.alias or name for name, field in model.model_fields.items()}


def test_contract_version_matches_the_schema():
    assert CONTRACT_VERSION == load_contract_schema()["contractVersion"]


def test_capability_model_matches_the_schema():
    declared = declared_properties("GraphDbCapabilities")
    fields = model_fields(GraphDbCapabilities)
    assert fields <= declared, f"model has fields the schema does not: {fields - declared}"
    required = set(schema_def("GraphDbCapabilities")["required"])
    assert required <= fields, f"schema requires fields the model lacks: {required - fields}"


def test_capability_report_adds_only_the_contract_version():
    assert model_fields(CapabilityReport) - model_fields(GraphDbCapabilities) == {"contractVersion"}


@pytest.mark.parametrize(
    "model,definition",
    [
        (ExpandRequest, "ExpandRequest"),
        (PullCategoryRequest, "PullCategoryRequest"),
        (PullRelationshipRequest, "PullRelationshipRequest"),
        (FulltextSearchRequest, "FulltextSearchRequest"),
    ],
)
def test_intent_models_match_the_schema(model, definition):
    assert model_fields(model) == declared_properties(definition)


def test_capabilities_serialize_under_the_wire_names():
    # `schema` is a pydantic-reserved-ish name, so the field is `schema_` with an
    # alias; the client reads `capabilities.schema.source`.
    payload = CapabilityReport(type="spanner").model_dump(by_alias=True)
    assert "schema" in payload and "schema_" not in payload
    assert payload["schema"]["source"] == "proxy"


def test_label_key_identity_requires_a_key_predicate():
    capabilities = GraphDbCapabilities(type="x")
    capabilities.identity.nodeId = "label-key"
    capabilities.expand.predicate = "internal-id"
    with pytest.raises(CapabilityError, match="requires expand.predicate"):
        assert_capabilities_consistent(capabilities)


def test_an_intent_may_not_claim_a_denied_feature():
    capabilities = GraphDbCapabilities(type="x", intents=["pullCategory"])
    with pytest.raises(CapabilityError, match="requires pull.category"):
        assert_capabilities_consistent(capabilities)

    searchable = GraphDbCapabilities(type="x", intents=["search"])
    with pytest.raises(CapabilityError, match="requires fulltextSearch.supported"):
        assert_capabilities_consistent(searchable)


def test_a_consistent_record_passes():
    capabilities = GraphDbCapabilities(type="spanner")
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    assert_capabilities_consistent(capabilities)


def test_expand_request_defaults_are_safe():
    request = ExpandRequest()
    assert request.nodeIds == []
    assert request.direction == "all"
    assert request.hops == 1
    assert request.onlyBetweenSelected is False
