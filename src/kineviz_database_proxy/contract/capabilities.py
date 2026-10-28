"""
What a proxied database can do, in the same shape the GraphXR client declares for
its native adapters.

Returned by ``GET /api/{type}/{project}/capabilities``. The client uses it to
decide whether to POST a typed intent (``/expand``, ``/pullCategory``, ...) or to
fall back to building the statement itself — the pre-refactor behaviour, which
hard-coded the Spanner dialect for every backend.

Mirrors ``shared/graphdb/capabilities.ts``; kept in step by ``tests/test_contract.py``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

CONTRACT_VERSION = "1.0.0"

NodeIdStrategy = Literal["internal-id", "label-key"]
ExpandPredicate = Literal["internal-id", "primary-key"]
ExpandDirection = Literal["all", "from", "to", "both"]
IntentName = Literal["expand", "pullCategory", "pullRelationship", "search"]
SchemaSourceKind = Literal["server-route", "client-probe", "proxy", "none"]


class IdentityCapabilities(BaseModel):
    """How a GraphXR node id is formed for this backend."""

    nodeId: NodeIdStrategy = "internal-id"


class ExpandCapabilities(BaseModel):
    """
    ``predicate`` is fixed per database, never chosen per call: a backend with no
    node-identity function can only re-select seeds by their primary key.
    """

    predicate: ExpandPredicate = "internal-id"
    directions: List[ExpandDirection] = Field(default_factory=lambda: ["all", "from", "to", "both"])
    multiHop: bool = False
    relationshipTypeFilter: bool = True
    internalRelationships: bool = False
    excludeRelationshipIds: bool = False


class SchemaCapabilities(BaseModel):
    source: SchemaSourceKind = "proxy"
    refreshOnWrite: bool = False


class FulltextSearchCapabilities(BaseModel):
    supported: bool = False
    manageIndex: bool = False


class PullCapabilities(BaseModel):
    category: bool = False
    relationship: bool = False
    excludeLoaded: bool = False


class GraphDbCapabilities(BaseModel):
    """One backend's full capability record."""

    type: str
    queryLanguages: List[str] = Field(default_factory=lambda: ["cypher"])
    defaultQueryLanguage: str = "cypher"
    identity: IdentityCapabilities = Field(default_factory=IdentityCapabilities)
    expand: ExpandCapabilities = Field(default_factory=ExpandCapabilities)
    schema_: SchemaCapabilities = Field(default_factory=SchemaCapabilities, alias="schema")
    fulltextSearch: FulltextSearchCapabilities = Field(default_factory=FulltextSearchCapabilities)
    pull: PullCapabilities = Field(default_factory=PullCapabilities)
    multiDatabase: bool = False
    write: bool = False
    intents: Optional[List[IntentName]] = None

    model_config = {"populate_by_name": True}


class CapabilityReport(GraphDbCapabilities):
    """The capability record plus the contract version the proxy was built against."""

    contractVersion: str = CONTRACT_VERSION


class CapabilityError(ValueError):
    """A capability record that contradicts itself."""


def assert_capabilities_consistent(capabilities: GraphDbCapabilities) -> None:
    """
    The same invariants ``assertCapabilitiesConsistent`` enforces on the client.

    Checked when a driver declares its capabilities, so a half-declared backend
    fails at startup instead of at the user's first click.
    """

    def fail(message: str) -> None:
        raise CapabilityError(f"[{capabilities.type}] {message}")

    if not capabilities.type:
        fail("type must be a non-empty string")

    if capabilities.queryLanguages and capabilities.defaultQueryLanguage not in capabilities.queryLanguages:
        fail(f'defaultQueryLanguage "{capabilities.defaultQueryLanguage}" is not in queryLanguages')

    if capabilities.identity.nodeId == "label-key" and capabilities.expand.predicate != "primary-key":
        fail('identity.nodeId "label-key" requires expand.predicate "primary-key"')

    if capabilities.expand.predicate == "primary-key" and capabilities.schema_.source == "none":
        fail('expand.predicate "primary-key" requires a schema source that supplies category keys')

    if not capabilities.expand.directions:
        fail("expand.directions must be non-empty")

    if capabilities.fulltextSearch.manageIndex and not capabilities.fulltextSearch.supported:
        fail("fulltextSearch.manageIndex requires fulltextSearch.supported")

    for intent in capabilities.intents or []:
        if intent == "search" and not capabilities.fulltextSearch.supported:
            fail('intent "search" requires fulltextSearch.supported')
        if intent == "pullCategory" and not capabilities.pull.category:
            fail('intent "pullCategory" requires pull.category')
        if intent == "pullRelationship" and not capabilities.pull.relationship:
            fail('intent "pullRelationship" requires pull.relationship')


@lru_cache(maxsize=1)
def load_contract_schema() -> Dict[str, Any]:
    """The vendored copy of ``shared/graphdb/contract.schema.json``."""
    path = Path(__file__).with_name("contract.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))
