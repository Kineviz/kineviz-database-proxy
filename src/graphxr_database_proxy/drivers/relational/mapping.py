"""
Load and check a mapping document: the override plus discovery, into one answer.

``from_document`` is the single authority on what a valid mapping is. The editor in
the UI promises the same rules — excluding a record drops the links that needed it,
a rename rewrites every endpoint that points at the old label — and those promises
live *here*, so an uploaded file obeys them exactly as a clicked one does.

Nothing in this module touches a database or a file. The ``catalog`` argument is the
draft document ``catalog.propose`` produced from a live schema, and it may be
``None``: a mapping is routinely written for a database its author cannot reach, so
shape-only validation has to work, and a mismatch with the live catalog is a
**warning**, never a blocker. The catalog is never re-read here.

Problems are ``(path, message)`` pairs, and the path is a control in the document —
``edges[1].endpoints[0].dst.label``, ``nodes[0].key`` — because a message that cannot
be pointed at becomes a form a person has to hunt through. One typo is reported
once: when a record is refused, the links that needed it are dropped with a warning
instead of each raising the same misspelling a second time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from ...models.graph_mapping import (
    UNKNOWN_TYPE,
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
)
from .schemas import Endpoint, NodeSchema, Pair, RelSchema

#: Letters, digits and underscore, not starting with a digit: what every one of the
#: three engines accepts unquoted, and what statement text is built from.
SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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

#: Kept in one sentence because a person has to be able to see that a composite key is
#: a contract limit, not a corner someone ran out of time on.
COMPOSITE_KEY_REASON = (
    "the graph contract carries one key pair per seed node (NodeRef.key_prop) and "
    "_expand_by_key reads group[0].key_prop, so a table keyed by more than one column "
    "cannot be expanded at all"
)

# Why an endpoint could not be placed. ``_PLACED`` is the success marker; the cascade
# costs a warning, and a problem has already been recorded and must not be told twice.
_PLACED = ""
_CASCADE = "cascade"
_ERROR = "error"


@dataclass
class EffectiveMapping:
    """
    The mapping a driver actually runs, and what it cost to get there.

    ``problems`` empty means this loads. ``provenance`` is keyed by label or type and
    says which of the entries below are discovery's doing and which are a person's,
    because the page mixes a machine proposal and a human decision and a person has to
    be able to tell which one they are overriding. The set is closed, and a props-only
    edit stays ``auto`` — "same record, different columns" has no word of its own.
    """

    nodes: Dict[str, NodeSchema] = field(default_factory=dict)
    edges: Dict[str, RelSchema] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    problems: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """True when nothing blocks this mapping from loading."""
        return not self.problems


def from_document(
    doc: Optional[GraphMapping], catalog: Optional[GraphMapping] = None
) -> EffectiveMapping:
    """
    Combine an override document with a discovered draft.

    ``doc`` is what a person declared, or ``None`` for "whatever discovery found".
    ``catalog`` is ``propose``'s draft, or ``None`` when there is no live schema to
    check against — the normal case for a file written offline.
    """
    if doc is None and catalog is None:
        return EffectiveMapping(
            warnings=[
                "no graph mapping and no catalog: nothing to serve — discover a schema "
                "or upload a mapping"
            ]
        )
    return _Loader(doc, catalog).run()


class _Loader:
    """One document, one pass, with the state the rules need kept off the module."""

    def __init__(self, doc: Optional[GraphMapping], catalog: Optional[GraphMapping]) -> None:
        self.doc = doc
        self.index = _CatalogIndex.of(catalog)
        self.scope = _Scope.of(doc)
        self.out = EffectiveMapping()
        #: Labels in effect. Endpoints are matched against these and nothing else.
        self.labels: Set[str] = set()
        #: Every name in effect, labels and types together, for the clash check.
        self.declared: Set[str] = set()
        #: What discovery called the table, so a rename rewrites the endpoints that
        #: were written against the old name.
        self.renames: Dict[str, str] = {}
        #: Records this document asked for and this refused to load. Links pointing at
        #: one are dropped with a warning, because the refusal is already on the screen.
        self.broken: Set[str] = set()
        #: Tables a record already took, so discovery cannot map one twice.
        self.claimed: Set[str] = set()
        #: Name -> the path of the entry that made it, so a clash has an address. The
        #: edge's own path wins: when one name means both, the link is the thing to
        #: rename, because every endpoint in the file already points at the record.
        self.paths: Dict[str, str] = {}
        self.edge_paths: Dict[str, str] = {}
        self.default_schema = (doc.schema_name if doc is not None else None) or (
            self.index.doc.schema_name if self.index.doc is not None else None
        )

    # -- pass order ----------------------------------------------------------

    def run(self) -> EffectiveMapping:
        """
        Records before links, and both kinds before the checks that span them.

        Discovery's records are read *before* the document's links, because an
        endpoint may point at a label nobody had to redeclare.
        """
        self.document_nodes()
        self.discovered_nodes()
        self.document_edges()
        self.discovered_edges()
        self.cross_checks()
        self.index.unused_scope_names(self.scope, self.out)
        return self.out

    # -- reporting -----------------------------------------------------------

    def problem(self, path: str, message: str) -> None:
        self.out.problems.append((path, message))

    def warn(self, path: str, message: str) -> None:
        """A catalog mismatch: said once, named by path, and never blocks a save."""
        self.out.warnings.append("{}: {}".format(path, message))

    def ident(self, value: object, path: str, what: str) -> str:
        """Check an identifier, and hand back "" when it is not one."""
        text = str(value or "")
        if not SAFE_IDENT.match(text):
            self.problem(
                path,
                "{!r} is not a legal {} (letters, digits and underscore, not starting "
                "with a digit)".format(text, what),
            )
            return ""
        return text

    def single_key(self, key: Optional[str], path: str, what: str) -> Optional[str]:
        """
        The one column, or None with the reason recorded.

        Refused rather than narrowed to the first column: a composite key quietly
        matched on one of its columns returns plausible-looking wrong rows, which is
        the worst thing a graph can do to an investigation.
        """
        parts = [part.strip() for part in str(key or "").split(",") if part.strip()]
        if len(parts) > 1:
            self.problem(
                path,
                "{} key {!r} is composite and {}; declare one of {} as a surrogate or "
                "single column".format(what, key, COMPOSITE_KEY_REASON, parts),
            )
            return None
        if not parts:
            return None
        return self.ident(parts[0], path, "{} key".format(what))

    def check_table(self, path: str, table: str) -> None:
        if self.index.live and table not in self.index.tables:
            self.warn(
                path,
                "table {!r} is not in this schema — another database, another schema, "
                "or gone".format(table),
            )

    def check_column(self, path: str, table: str, column: str) -> None:
        if not self.index.live:
            return
        known = self.index.columns.get(table)
        if known and column not in known:
            self.warn(path, "column {!r} is not a column of {!r} in this schema".format(column, table))

    # -- records -------------------------------------------------------------

    def document_nodes(self) -> None:
        if self.doc is None:
            return
        for i, node in enumerate(self.doc.nodes):
            path = "nodes[{}]".format(i)
            label = self.ident(node.label, path + ".label", "node label")
            table = self.ident(node.table or node.label, path + ".table", "table name")
            if not label or not table:
                self.broken.add(str(node.label))
                continue
            if not node.include or self.scope.out([node.label, table]):
                # A record the person took out stays out: discovery has the same table,
                # and putting it back would undo the untick that produced this.
                self.out.provenance[label] = PROVENANCE_EXCLUDED
                self.claimed.add(table)
                continue
            if label in self.out.nodes:
                self.problem(path + ".label", "duplicate node label {!r}".format(label))
                continue
            if table in self.claimed:
                self.problem(
                    path + ".table",
                    "table {!r} is already mapped; one table cannot be two labels and "
                    "still round-trip an id".format(table),
                )
                continue
            self.claimed.add(table)
            self.check_table(path + ".table", table)

            found = self.index.node_for_table(table)
            key = self.node_key(node, found, table, path)
            if key is None:
                self.broken.add(label)
                continue
            props = _props(node, table, self.index, {key}, path, self.out)
            key_type = self.index.column_type(table, key) or node.properties.get(key) or UNKNOWN_TYPE
            self.add_node(node, label, table, key, key_type, props, path, _node_kind(node, found))
            if found is not None and found.label != node.label:
                self.renames[found.label] = label

    def node_key(
        self, node: MappingNode, found: Optional[MappingNode], table: str, path: str
    ) -> Optional[str]:
        """A node needs a single-column key: stated, else discovery's, else refused."""
        if node.key:
            key = self.single_key(node.key, path + ".key", "node")
            if key is not None:
                self.check_column(path + ".key", table, key)
                if found is not None and not found.include:
                    self.warn(
                        path + ".key",
                        "discovery left {!r} out because it could not key it; {!r} is "
                        "being mapped anyway, and expand will fail on it unless it is "
                        "both unique and not null".format(table, key),
                    )
            return key
        discovered = found.key if found is not None else self.index.key_for_table(table)
        if discovered:
            self.warn(
                path + ".key",
                "no key declared, using {!r} as discovery found it".format(discovered),
            )
            return self.single_key(discovered, path + ".key", "node")
        self.problem(
            path + ".key",
            "a node needs a single-column key and none is declared: {}".format(
                COMPOSITE_KEY_REASON
            ),
        )
        return None

    def add_node(
        self,
        node: MappingNode,
        label: str,
        table: str,
        key: str,
        key_type: str,
        props: Dict[str, str],
        path: str,
        kind: str,
    ) -> None:
        self.out.nodes[label] = NodeSchema(
            label=label,
            table=table,
            key=key,
            key_type=key_type,
            props=props,
            schema=node.schema_name or self.default_schema,
            show_as=node.show_as,
            notes=node.notes or "",
        )
        self.labels.add(label)
        self.declared.add(label)
        self.paths.setdefault(label, path)
        self.out.provenance.setdefault(label, kind)

    def discovered_nodes(self) -> None:
        """Records discovery found that the document never mentioned, in as they stand."""
        for node in self.index.nodes():
            table = node.table or node.label
            if node.label in self.declared or table in self.claimed:
                continue
            if self.out.provenance.get(node.label) == PROVENANCE_EXCLUDED:
                continue
            path = "auto.nodes[{}]".format(node.label)
            if not node.include or self.scope.out([node.label, table]):
                self.out.provenance.setdefault(node.label, PROVENANCE_EXCLUDED)
                continue
            key = self.single_key(node.key, path + ".key", "node")
            if key is None:
                # Discovery already said why in `notes`; a table it could not key is
                # out of the graph, and the reason stays with it.
                self.out.provenance.setdefault(node.label, PROVENANCE_EXCLUDED)
                continue
            self.claimed.add(table)
            props = _props(node, table, self.index, {key}, path, self.out)
            self.add_node(
                node,
                node.label,
                table,
                key,
                self.index.column_type(table, key) or UNKNOWN_TYPE,
                props,
                path,
                PROVENANCE_AUTO,
            )

    # -- links ---------------------------------------------------------------

    def document_edges(self) -> None:
        if self.doc is None:
            return
        for i, edge in enumerate(self.doc.edges):
            self.add_edge(edge, "edges[{}]".format(i), from_document=True)

    def discovered_edges(self) -> None:
        for edge in self.index.edges():
            if edge.type in self.declared:
                continue
            if self.out.provenance.get(edge.type) == PROVENANCE_EXCLUDED:
                # Either the person took it out, or a record it needs was taken out and
                # it went with it. Discovery must not quietly put either back.
                continue
            self.add_edge(
                edge, "auto.edges[{}]".format(edge.type), from_document=False
            )

    def add_edge(self, edge: MappingEdge, path: str, from_document: bool) -> None:
        """
        One edge type, from either source.

        ``from_document`` is what makes a missing key a refusal instead of a fallback:
        only a person's file can be silent about a table nobody can see, and then it
        must say so on purpose.
        """
        etype = self.ident(edge.type, path + ".type", "relationship type")
        table = self.ident(edge.table or edge.type, path + ".table", "table name")
        if not etype or not table:
            return
        if not edge.include or self.scope.out([edge.type, table]):
            self.out.provenance[etype] = PROVENANCE_EXCLUDED
            return
        if etype in self.out.edges:
            self.problem(path + ".type", "duplicate edge type {!r}".format(etype))
            return
        if not edge.endpoints:
            self.problem(
                path + ".endpoints",
                "an edge with no endpoint pair is a relationship that can never be "
                "drawn; declare at least one src/dst",
            )
            return
        if from_document:
            self.check_table(path + ".table", table)
        found = self.index.edge_for(edge) if from_document else edge
        pairs, state = self.pairs(edge, path)
        if state == _ERROR:
            return
        if state == _CASCADE:
            self.out.provenance[etype] = PROVENANCE_EXCLUDED
            return
        key, key_type = self.edge_key(edge, found, table, path, from_document)
        if key is None and edge.key:
            return
        props = _props(
            edge,
            table,
            self.index,
            _columns_of(pairs) | ({key} if key else set()),
            path,
            self.out,
        )
        self.out.edges[etype] = RelSchema(
            type=etype,
            table=table,
            key=key,
            key_type=key_type,
            endpoints=pairs,
            props=props,
            schema=edge.schema_name or self.default_schema,
            show_as=edge.show_as,
            notes=edge.notes or "",
        )
        self.declared.add(etype)
        self.paths.setdefault(etype, path)
        self.edge_paths.setdefault(etype, path)
        self.out.provenance.setdefault(
            etype,
            PROVENANCE_FLIPPED
            if edge.flip
            else (PROVENANCE_AUTO if found is not None else PROVENANCE_ADDED),
        )

    def edge_key(
        self,
        edge: MappingEdge,
        found: Optional[MappingEdge],
        table: str,
        path: str,
        from_document: bool,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        An edge prefers its own key, so parallel links stay distinct.

        Stated, then discovery's for this table, then nothing. "Nothing" mints the id
        from the two endpoint keys sorted, which is direction-stable but collapses
        duplicates — a warning when a schema was there to establish the table has no
        key, and a refusal in a hand-written file, where silence would be a graph with
        links missing from it.
        """
        stated = edge.key or (found.key if found is not None else None)
        if stated:
            key = self.single_key(stated, path + ".key", "edge")
            if key is None:
                return None, None
            self.check_column(path + ".key", table, key)
            return key, self.index.column_type(table, key) or UNKNOWN_TYPE
        if from_document and not self.index.live:
            self.problem(
                path + ".key",
                "an edge needs its own key so parallel links stay distinct, and none is "
                "declared; with no catalog to find {!r}'s primary key, name the "
                "column".format(table),
            )
            return None, None
        self.warn(
            path + ".key",
            "{!r} has no key of its own, so its id is minted from the two endpoint keys "
            "sorted: direction-stable, but duplicate links between the same two records "
            "collapse onto one id".format(table),
        )
        return None, None

    def pairs(self, edge: MappingEdge, path: str) -> Tuple[List[Pair], str]:
        """
        The endpoint pairs, each label put back through the rename map.

        ``_ERROR``: a problem was recorded and the edge does not load. ``_CASCADE``:
        an end needed a record this document excluded, which drops the link and says
        which one, rather than refusing the file.
        """
        pairs: List[Pair] = []
        state = _PLACED
        for j, raw in enumerate(edge.endpoints):
            where = "{}.endpoints[{}]".format(path, j)
            src, src_state = self.endpoint(edge, raw.src, "src", where)
            dst, dst_state = self.endpoint(edge, raw.dst, "dst", where)
            if src is None or dst is None:
                if _ERROR in (src_state, dst_state):
                    state = _ERROR
                elif state == _PLACED:
                    state = _CASCADE
                continue
            pairs.append(Pair(src=src, dst=dst, row_estimate=raw.row_estimate))
        if state == _ERROR:
            return pairs, _ERROR
        if edge.flip:
            pairs = [pair.swapped() for pair in pairs]
        return _deduped(pairs), state

    def endpoint(
        self, edge: MappingEdge, end: MappingEndpoint, side: str, where: str
    ) -> Tuple[Optional[Endpoint], str]:
        """One end of one pair, resolved to a label this mapping actually declares."""
        path = "{}.{}".format(where, side)
        label = self.ident(end.label, path + ".label", "node label")
        column = self.ident(end.column, path + ".column", "column name")
        if not label or not column:
            return None, _ERROR
        resolved = self.renames.get(label, label)
        if resolved not in self.labels:
            if resolved in self.broken:
                # The record it needs already failed to load, and its own problem is on
                # the screen. Saying so once is what keeps a form from lighting up six
                # controls for the one the person has to fix.
                self.warn(
                    path + ".label",
                    "{!r} is dropped because the record {!r} it needs did not load".format(
                        edge.type, label
                    ),
                )
                return None, _CASCADE
            if self.out.provenance.get(resolved) == PROVENANCE_EXCLUDED:
                self.warn(
                    path + ".label",
                    "{!r} is dropped because the record {!r} it needs is not in the "
                    "graph".format(edge.type, label),
                )
                return None, _CASCADE
            self.problem(
                path + ".label",
                "label {!r} is not declared under nodes; a typo here is an edge type "
                "that silently never matches. Declared labels: {}".format(
                    label, ", ".join(sorted(self.labels)) or "(none)"
                ),
            )
            return None, _ERROR
        table = edge.table or edge.type
        self.check_column(path + ".column", table, column)
        type_name = end.column_type or self.index.column_type(table, column) or UNKNOWN_TYPE
        return Endpoint(label=resolved, column=column, column_type=type_name), _PLACED

    # -- across both kinds -----------------------------------------------------

    def cross_checks(self) -> None:
        """A name used for both a node and an edge is refused: ``<Name>:<key>`` would be two things."""
        for name in sorted(set(self.out.nodes) & set(self.out.edges)):
            where = self.edge_paths.get(name)
            path = "{}.type".format(where) if where else self.paths.get(name, "edges")
            self.problem(
                path,
                "{!r} is used for both a node and an edge; one name has to mean one "
                "thing or the id <{}>:<key> is ambiguous".format(name, name),
            )
            del self.out.edges[name]


# ------------------------------------------------------------------ document scope


class _Scope:
    """The ``include`` / ``exclude`` lists, matched against a record's label or its table."""

    def __init__(self, whitelist: Sequence[str], blacklist: Sequence[str]) -> None:
        self.whitelist = list(whitelist)
        self.blacklist = list(blacklist)
        self.matched: Set[str] = set()

    @classmethod
    def of(cls, doc: Optional[GraphMapping]) -> "_Scope":
        if doc is None:
            return cls([], [])
        return cls(list(doc.include), list(doc.exclude))

    def out(self, names: Sequence[str]) -> bool:
        """True when this record is not in the graph, marking the names that decided it."""
        keys = {str(name).casefold() for name in names if name}
        if self.whitelist and not _any_hit(self.whitelist, keys, self.matched):
            return True
        return _any_hit(self.blacklist, keys, self.matched)


def _any_hit(names: Sequence[str], keys: Set[str], matched: Set[str]) -> bool:
    found = False
    for name in names:
        if name.casefold() in keys:
            matched.add(name)
            found = True
    return found


# ------------------------------------------------------------------ catalog index


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


# ------------------------------------------------------------------ helpers


_Record = Union[MappingNode, MappingEdge]


def _props(
    record: _Record,
    table: str,
    index: _CatalogIndex,
    drop: Set[str],
    path: str,
    out: EffectiveMapping,
) -> Dict[str, str]:
    """
    The columns this record exposes, typed the way the live schema says they are.

    A stated list is a whitelist and stays the authority — that is exactly what makes
    an unmapped column invisible, which is why the editor counts them. Empty means
    take discovery's columns. Either way a known table corrects a type, because
    binding a parameter as the wrong type costs a full scan and raises no error.
    """
    excluded = {str(name) for name in record.exclude_properties}
    source = dict(record.properties)
    if not source:
        source = dict(index.columns.get(table, {}))
    else:
        for name in list(source):
            actual = index.column_type(table, name)
            if actual is not None:
                source[name] = actual
            elif index.live and index.columns.get(table) and name not in drop and name not in excluded:
                out.warnings.append(
                    "{}.properties: column {!r} is not a column of {!r} in this "
                    "schema".format(path, name, table)
                )
    return {
        name: type_name
        for name, type_name in source.items()
        if name not in excluded and name not in drop
    }


def _node_kind(node: MappingNode, found: Optional[MappingNode]) -> str:
    """Discovery's record, a person's rename of it, or something no foreign key proposed."""
    if found is None:
        return PROVENANCE_ADDED
    return PROVENANCE_RENAMED if found.label != node.label else PROVENANCE_AUTO


def _columns_of(pairs: Sequence[Pair]) -> Set[str]:
    out: Set[str] = set()
    for pair in pairs:
        out.add(pair.src.column)
        out.add(pair.dst.column)
    return out


def _deduped(pairs: Sequence[Pair]) -> List[Pair]:
    """The same two ends named twice is one pair, and the bigger count is the guess to keep."""
    seen: Dict[Tuple[str, str, str, str], Pair] = {}
    for pair in pairs:
        mark = (pair.src.label, pair.src.column, pair.dst.label, pair.dst.column)
        keep = seen.get(mark)
        if keep is None or pair.row_estimate > keep.row_estimate:
            seen[mark] = pair
    return list(seen.values())
