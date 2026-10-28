"""
The four typed graph intents, implemented once for every driver.

A driver mixes this in and supplies two things: its ``StatementDialect`` and its
capability record. Everything else — turning node ids into predicates, fanning out
per category, merging the graphs — is here, mirroring
``BaseGraphDbAdapter`` on the GraphXR client.

Before this, statement generation lived in the browser and was hard-coded to the
Spanner dialect for every backend, so a RocketGraph project emitted statements its
backend could not parse.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..contract.capabilities import GraphDbCapabilities
from ..contract.intents import (
    ExpandRequest,
    FulltextSearchRequest,
    PullCategoryRequest,
    PullRelationshipRequest,
)
from ..models.project import GraphData, QueryData
from .dialect import NodeRef, StatementDialect, build_expand, build_pull_category, build_pull_relationship


def split_label_key_id(node_id: str) -> Optional[tuple]:
    """
    ``Person:42`` -> ``("Person", "42")``, splitting on the **first** colon so a key
    containing colons survives. None when there is no label or no key part.
    """
    text = str(node_id)
    separator = text.find(":")
    if separator <= 0 or separator == len(text) - 1:
        return None
    return text[:separator], text[separator + 1 :]


def merge_graph_results(results: Sequence[QueryData]) -> QueryData:
    """De-duplicate by id across a fan-out, keeping first-seen order."""
    nodes: Dict[str, Any] = {}
    relationships: Dict[str, Any] = {}
    for result in results:
        if not result or result.type != "GRAPH" or not isinstance(result.data, GraphData):
            continue
        for node in result.data.nodes:
            nodes.setdefault(str(node.id), node)
        for relationship in result.data.relationships:
            relationships.setdefault(str(relationship.id), relationship)
    return QueryData(
        type="GRAPH",
        data=GraphData(nodes=list(nodes.values()), relationships=list(relationships.values())),
    )


class GraphIntentMixin:
    """
    Requires the host driver to provide:

      - ``graph_dialect`` -> StatementDialect
      - ``graph_capabilities`` -> GraphDbCapabilities
      - ``execute_query(query, parameters)`` -> QueryResponse   (BaseDatabaseDriver)
      - ``get_graph_schema()`` -> GraphSchemaResponse           (BaseDatabaseDriver)
      - ``graph_key_types()`` -> {category: key type}           (optional)
    """

    graph_dialect: StatementDialect
    graph_capabilities: GraphDbCapabilities

    def graph_key_types(self) -> Dict[str, str]:
        """Category -> its key property's type, for literal formatting."""
        return {}

    def graph_categories(self) -> Dict[str, Any]:
        """Category name -> schema metadata, used to find each category's key."""
        return {}

    def remember_graph_categories(self, categories: Dict[str, Any]) -> None:
        """Overridden by ``GraphIntentSupport``; a no-op for a driver without it."""

    async def _ensure_graph_categories(self) -> None:
        """
        Load the graph schema when the backend needs it and does not have it yet.

        A ``primary-key`` backend cannot turn a node id into a predicate without
        knowing which property is that category's key, and the API builds a fresh
        driver per request — so nothing carries the schema over from an earlier
        call. Without this, every ``/expand`` on such a backend resolved zero seeds
        and answered with an empty graph rather than an error.

        Backends with an identity function need none of this and are left alone, so
        no extra round-trip is introduced where it buys nothing.
        """
        if self.graph_capabilities.expand.predicate != "primary-key":
            return
        if self.graph_categories():
            return
        # Through the cache where the host has one: a run of intents would
        # otherwise re-probe the store per request, from a cold connection, for an
        # answer none of them changed. The mixin only requires the plain method,
        # so a host without the base driver's cache still works.
        load = getattr(self, "get_graph_schema_cached", None) or self.get_graph_schema  # type: ignore[attr-defined]
        response = await load()
        if not response or not response.success or not response.data:
            return
        self.remember_graph_categories(
            {category.name: category.model_dump() for category in response.data.categories}
        )

    # -- seed resolution ----------------------------------------------------

    def _resolve_refs(self, node_ids: Sequence[str], category: Optional[str] = None) -> List[NodeRef]:
        """
        Turn the client's node ids into predicate inputs.

        With ``label-key`` identity everything needed is inside the id. With
        ``internal-id`` identity the id *is* the predicate value.
        """
        capabilities = self.graph_capabilities
        categories = self.graph_categories()
        refs: List[NodeRef] = []

        for node_id in node_ids or []:
            text = str(node_id)
            if not text:
                continue

            if capabilities.identity.nodeId == "label-key":
                parsed = split_label_key_id(text)
                if not parsed:
                    continue
                label, key = parsed
                if category and label != category:
                    continue
                keys = (categories.get(label) or {}).get("keys") or []
                if not keys:
                    continue
                refs.append(NodeRef(category=label, internal_id=text, key_prop=str(keys[0]), key_value=key))
            else:
                refs.append(NodeRef(category=category or "", internal_id=text))

        return refs

    async def _run_statements(self, statements: Sequence[str]) -> QueryData:
        results: List[QueryData] = []
        for statement in statements:
            response = await self.execute_query(statement, {})  # type: ignore[attr-defined]
            if response and response.data:
                results.append(response.data)
        if len(results) == 1:
            return results[0]
        return merge_graph_results(results)

    # -- the four intents ---------------------------------------------------

    async def expand(self, request: ExpandRequest) -> QueryData:
        await self._ensure_graph_categories()
        capabilities = self.graph_capabilities
        direction = (
            request.direction
            if request.direction in capabilities.expand.directions
            else capabilities.expand.directions[0]
        )
        statements = build_expand(
            self.graph_dialect,
            self._resolve_refs(request.nodeIds, request.category),
            direction=direction,
            relationships=request.relationships if capabilities.expand.relationshipTypeFilter else [],
            exclude_relationship_types=request.excludeRelationshipTypes,
            exclude_relationship_ids=(
                request.excludeRelationshipIds if capabilities.expand.excludeRelationshipIds else []
            ),
            hops=request.hops if capabilities.expand.multiHop else 1,
            only_between_selected=request.onlyBetweenSelected,
            limit=request.limit,
            skip=request.skip,
            category=request.category,
            key_types=self.graph_key_types(),
        )
        return await self._run_statements(statements)

    async def pull_category(self, request: PullCategoryRequest) -> QueryData:
        if not self.graph_capabilities.pull.category:
            return QueryData(type="GRAPH", data=GraphData())
        await self._ensure_graph_categories()
        loaded = (
            self._resolve_refs(request.loadedNodeIds, request.category)
            if self.graph_capabilities.pull.excludeLoaded
            else []
        )
        statements = build_pull_category(
            self.graph_dialect,
            request.category,
            limit=request.limit,
            skip=request.skip,
            loaded=loaded,
            key_types=self.graph_key_types(),
        )
        return await self._run_statements(statements)

    async def pull_relationship(self, request: PullRelationshipRequest) -> QueryData:
        if not self.graph_capabilities.pull.relationship:
            return QueryData(type="GRAPH", data=GraphData())
        statements = build_pull_relationship(
            self.graph_dialect,
            request.relationship,
            limit=request.limit,
            skip=request.skip,
            loaded_ids=(
                request.loadedRelationshipIds if self.graph_capabilities.pull.excludeLoaded else []
            ),
        )
        return await self._run_statements(statements)

    async def fulltext_search(self, request: FulltextSearchRequest) -> QueryData:
        """Overridden by drivers whose backend has a searchable index."""
        raise NotImplementedError(
            f"{self.graph_capabilities.type} does not support full-text search"
        )
