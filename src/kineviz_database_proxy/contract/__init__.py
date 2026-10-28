"""
The GraphXR graph-database contract, mirrored from ``shared/graphdb`` in the
graphxr-dev repository.

The TypeScript definitions there are normative; ``contract.schema.json`` is the
machine-readable form of them, vendored here and validated against these models
by ``tests/test_contract.py``. A field added on one side and forgotten on the
other therefore fails a test rather than becoming a runtime mismatch between two
repositories.

See ai/graph-db-adapter-refactoring-spec.md section 8.2 in graphxr-dev.
"""

from .capabilities import (
    CONTRACT_VERSION,
    CapabilityReport,
    ExpandCapabilities,
    ExpandDirection,
    ExpandPredicate,
    FulltextSearchCapabilities,
    GraphDbCapabilities,
    IdentityCapabilities,
    IntentName,
    NodeIdStrategy,
    PullCapabilities,
    SchemaCapabilities,
    load_contract_schema,
)
from .intents import (
    ExpandRequest,
    FulltextSearchRequest,
    PullCategoryRequest,
    PullRelationshipRequest,
)

__all__ = [
    "CONTRACT_VERSION",
    "CapabilityReport",
    "ExpandCapabilities",
    "ExpandDirection",
    "ExpandPredicate",
    "ExpandRequest",
    "FulltextSearchCapabilities",
    "FulltextSearchRequest",
    "GraphDbCapabilities",
    "IdentityCapabilities",
    "IntentName",
    "NodeIdStrategy",
    "PullCapabilities",
    "PullCategoryRequest",
    "PullRelationshipRequest",
    "SchemaCapabilities",
    "load_contract_schema",
]
