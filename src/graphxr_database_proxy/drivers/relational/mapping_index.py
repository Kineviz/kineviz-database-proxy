"""
The catalog side of loading a mapping: the draft ``catalog.propose`` wrote, walked
once into lookup maps.

Every question a rule asks about discovery — what did it call this table, what type
is this column, is there a schema here at all — is answered from these maps instead
of a second scan of the records. This is also where a scope name that matched nothing
gets said, because that needs both the names and the live schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set

from ...models.graph_mapping import UNKNOWN_TYPE

if TYPE_CHECKING:
    from ...models.graph_mapping import GraphMapping, MappingEdge, MappingNode
    from .mapping import EffectiveMapping
    from .mapping_scope import _Scope


class _CatalogIndex:
    """The discovered draft, indexed once so no rule below has to re-scan it."""

    def __init__(self, doc: Optional[GraphMapping]) -> None:
        self.doc = doc
        self.nodes_by_label: Dict[str, MappingNode] = {}
        self.nodes_by_table: Dict[str, MappingNode] = {}
        self.edges_by_type: Dict[str, MappingEdge] = {}
        self.edges_by_table: Dict[str, MappingEdge] = {}
        #: table -> column -> type, for every table discovery could name.
        self.columns: Dict[str, Dict[str, str]] = {}
        for node in self.nodes():
            table = node.table or node.label
            self.nodes_by_label[node.label] = node
            self.nodes_by_table.setdefault(table, node)
            known = self.columns.setdefault(table, {})
            if node.key:
                known[node.key] = UNKNOWN_TYPE
            known.update(node.properties)
        for edge in self.edges():
            table = edge.table or edge.type
            self.edges_by_type.setdefault(edge.type, edge)
            self.edges_by_table.setdefault(table, edge)
            known = self.columns.setdefault(table, {})
            for pair in edge.endpoints:
                for end in (pair.src, pair.dst):
                    known[end.column] = end.column_type or UNKNOWN_TYPE
            if edge.key:
                known.setdefault(edge.key, UNKNOWN_TYPE)
            known.update(edge.properties)

    def nodes(self) -> List[MappingNode]:
        return list(self.doc.nodes) if self.doc is not None else []

    def edges(self) -> List[MappingEdge]:
        return list(self.doc.edges) if self.doc is not None else []

    @classmethod
    def of(cls, doc: Optional[GraphMapping]) -> "_CatalogIndex":
        return cls(doc)

    @property
    def live(self) -> bool:
        """Whether there is a schema to check a document against at all."""
        return self.doc is not None

    @property
    def tables(self) -> Set[str]:
        return set(self.columns)

    def node_for_table(self, table: str) -> Optional[MappingNode]:
        return self.nodes_by_table.get(table)

    def edge_for(self, edge: MappingEdge) -> Optional[MappingEdge]:
        """Discovery's version of an edge: the same type first, then the table it lives on."""
        found = self.edges_by_type.get(edge.type)
        if found is None and edge.table:
            found = self.edges_by_table.get(edge.table)
        return found

    def key_for_table(self, table: str) -> Optional[str]:
        """The key discovery settled on for this table, from its link or its record."""
        edge = self.edges_by_table.get(table)
        if edge is not None:
            return edge.key
        node = self.nodes_by_table.get(table)
        return node.key if node is not None else None

    def column_type(self, table: str, column: str) -> Optional[str]:
        return self.columns.get(table, {}).get(column)

    def unused_scope_names(self, scope: _Scope, out: EffectiveMapping) -> None:
        """An include/exclude that names nothing is worth saying once, and only with a schema."""
        if not self.live:
            return
        for name in list(scope.whitelist) + list(scope.blacklist):
            if name not in scope.matched:
                out.warnings.append(
                    "scope: {!r} names no record in this schema (a name matches either "
                    "its label or its table)".format(name)
                )
