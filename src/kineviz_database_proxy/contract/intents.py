"""
The typed graph operations the GraphXR client POSTs to the proxy.

These are the request bodies of ``/expand``, ``/pullCategory``,
``/pullRelationship`` and ``/search``. Moving statement generation behind them is
what lets the client stay dialect-free: before, it built the statement itself and
hard-coded the Spanner dialect for every backend.

Mirrors ``shared/graphdb/intents.ts``; kept in step by ``tests/test_contract.py``.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .capabilities import ExpandDirection


class PagingRequest(BaseModel):
    limit: int = Field(default=1000, ge=0)
    skip: int = Field(default=0, ge=0)
    ignoreTips: Optional[bool] = None


class ExpandRequest(PagingRequest):
    """Neighbourhood expansion from a set of already-loaded nodes."""

    nodeIds: List[str] = Field(default_factory=list)
    #: Narrow the seeds to a single category.
    category: Optional[str] = None
    #: Relationship types to include; empty means any.
    relationships: List[str] = Field(default_factory=list)
    direction: ExpandDirection = "all"
    #: 1 = direct neighbours. Honoured only when the backend declares multiHop.
    hops: int = Field(default=1, ge=1)
    #: Return only edges whose other endpoint is also in ``nodeIds``.
    onlyBetweenSelected: bool = False
    #: Relationship ids already on the canvas.
    excludeRelationshipIds: List[str] = Field(default_factory=list)
    #: Relationship types the user has hidden; never re-selected.
    excludeRelationshipTypes: List[str] = Field(default_factory=list)


class PullCategoryRequest(PagingRequest):
    category: str
    #: Node ids already on the canvas, excluded when the backend supports it.
    loadedNodeIds: List[str] = Field(default_factory=list)


class PullRelationshipRequest(PagingRequest):
    relationship: str
    loadedRelationshipIds: List[str] = Field(default_factory=list)


class FulltextSearchRequest(PagingRequest):
    keyword: str
    indexName: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    #: Return per-category counts instead of nodes.
    countOnly: bool = False
    #: Neo4j-only: use the legacy APOC index.
    useApoc: bool = False
