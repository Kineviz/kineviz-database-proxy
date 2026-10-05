"""
Who decided each record: the closed set of kinds a page may show, and the rule that
picks the kind for a node the document declared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ...models.graph_mapping import MappingNode

PROVENANCE_AUTO = "auto"
PROVENANCE_RENAMED = "renamed"
PROVENANCE_FLIPPED = "flipped"
PROVENANCE_ADDED = "added"
PROVENANCE_EXCLUDED = "excluded"
PROVENANCE_KINDS = (
    PROVENANCE_AUTO,
    PROVENANCE_RENAMED,
    PROVENANCE_FLIPPED,
    PROVENANCE_ADDED,
    PROVENANCE_EXCLUDED,
)


def _node_kind(node: MappingNode, found: Optional[MappingNode]) -> str:
    """Discovery's record, a person's rename of it, or something no foreign key proposed."""
    if found is None:
        return PROVENANCE_ADDED
    return PROVENANCE_RENAMED if found.label != node.label else PROVENANCE_AUTO
