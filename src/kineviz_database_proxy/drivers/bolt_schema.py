# -*- coding: utf-8 -*-
"""
Schema discovery for the bolt family.

Neither Neo4j nor Memgraph has a schema to read: there is no ``INFORMATION_SCHEMA``
and no property-graph metadata document, so the shape has to be *probed* by
querying the store. The Python twin of
``web/react_views/configure/graphdb/databases/{neo4j,memgraph}/schemaProbe.ts`` in
graphxr-dev, which is normative.

**Neo4j** — the shape, then the property types:

  1. ``CALL db.schema.visualization()`` for the labels, the relationship types, and
     which ``(start, type, end)`` triples exist;
  2. ``CALL db.schema.nodeTypeProperties()`` and ``CALL db.schema.relTypeProperties()``,
     run together, for the properties and their declared types.

The procedures are the *accurate* answer, not the cheap one. They report every
property a label has ever held, with the type the store recorded, where the older
probe — one ``MATCH (n:L) RETURN keys(n), properties(n) LIMIT 1`` per label
UNION'd, and the same per relationship triple — reads a single record per label and
therefore misses any property that record happened not to carry, and guesses the
type from the value it did.

They pay for it by scanning. Measured on neo4j 5.26 (see
``tests/test_bolt_schema.py`` for the shapes):

  ==============  ====================  ==================
  graph           the two procedures    the sampling probe
  ==============  ====================  ==================
  200 nodes                     ~58 ms              ~11 ms
  125 000 nodes                ~250 ms              ~17 ms
  ==============  ====================  ==================

So the two costs grow along different axes: the procedures with the amount of
*data*, the sampling probe with the size of the *schema* — it grows a UNION branch
per label and per triple, and each branch scans until its first match. Neither is
universally cheaper. ``db.schema.visualization()`` scans neither; it stayed ~16 ms
across both graphs.

The sampling probe also remains the fallback for a server that has no such
procedures.

**Memgraph** — two routes, in order:

  1. ``SHOW SCHEMA INFO``, a single row holding a JSON document with ``nodes[]`` and
     ``edges[]`` and the types each property was actually seen with. It costs no
     scan, but is only available when the server was started with
     ``--schema-info-enabled=true``, which is off by default — so its absence is
     the normal case rather than an error.
  2. Neo4j's sampling probe over an inventory read off the store, because Memgraph
     has no ``db.schema.visualization()``, no ``db.labels()`` and no ``SHOW LABELS``.
     Reusing the Neo4j probe there made the very first statement throw and left
     every Memgraph project with no categories at all.

Mapping notes that apply to both:

  - A node carries a *set* of labels while a category is keyed by one label, so a
    label set is exploded: every label in it receives that node's properties, and
    an edge between two label sets becomes one triple per (start, end) pair.
  - A node with no labels contributes nothing, and an edge touching one is dropped.
  - Every table is read by column **name**, never by position: Memgraph does not
    preserve the ``RETURN`` order across a ``UNION``.

The builders and parsers are pure; only ``load_*_schema`` does I/O, and it does it
through a ``run`` callable so a test can drive it without a server.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from ..models.project import Category, GraphData, GraphSchema, QueryData, Relationship
from .bolt_mapping import infer_value_type, normalize_type_name, table_rows
from .dialect import backtick, cypher_string

VISUALIZATION_STATEMENT = "CALL db.schema.visualization()"

#: Every property each label and relationship type holds, with its recorded type.
#: Both answer a table; the node one is keyed by a *label set*, the relationship
#: one by a type. Present since Neo4j 3.5, and absent from Memgraph. Both scan the
#: store, so their cost grows with the data -- see the module docstring.
NODE_TYPE_PROPERTIES_STATEMENT = "CALL db.schema.nodeTypeProperties()"
REL_TYPE_PROPERTIES_STATEMENT = "CALL db.schema.relTypeProperties()"

#: More triples than this and relationship props are probed per type, not per
#: triple, so a dense schema cannot produce a UNION with hundreds of branches.
TRIPLE_FANOUT_LIMIT = 20

SHOW_SCHEMA_INFO_STATEMENT = "SHOW SCHEMA INFO"
NODE_INVENTORY_STATEMENT = "MATCH (n) RETURN DISTINCT labels(n) AS labels"
EDGE_INVENTORY_STATEMENT = (
    "MATCH (n)-[r]->(m) RETURN DISTINCT type(r) AS relName, labels(n) AS startL, labels(m) AS endL"
)

#: ``run(statement) -> QueryData``. Returns None when the statement failed, which
#: for several of these is an expected answer rather than a fault.
Run = Callable[[str], Awaitable[Optional[QueryData]]]


@dataclass(frozen=True)
class Triple:
    """One ``(relationship type, start label, end label)`` the store contains."""

    name: str
    start: str
    end: str


@dataclass
class Inventory:
    labels: List[str] = field(default_factory=list)
    triples: List[Triple] = field(default_factory=list)


@dataclass
class SampledProps:
    props: List[str] = field(default_factory=list)
    props_types: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Neo4j: db.schema.visualization()
# ---------------------------------------------------------------------------


def parse_visualization(result: Optional[QueryData]) -> Inventory:
    """
    ``db.schema.visualization()`` returns a graph of meta-nodes and meta-edges.

    A meta-edge whose endpoints are not both present is dropped — the pre-refactor
    client did the same, silently.
    """
    if result is None or result.type != "GRAPH" or not isinstance(result.data, GraphData):
        return Inventory()

    label_by_id: Dict[str, str] = {}
    for node in result.data.nodes:
        if node.labels:
            label_by_id[str(node.id)] = str(node.labels[0])

    inventory = Inventory(labels=list(dict.fromkeys(label_by_id.values())))
    seen: set = set()
    for edge in result.data.relationships:
        start = label_by_id.get(str(edge.startNodeId))
        end = label_by_id.get(str(edge.endNodeId))
        if not start or not end:
            continue
        triple = Triple(name=str(edge.type), start=start, end=end)
        if triple in seen:
            continue
        seen.add(triple)
        inventory.triples.append(triple)
    return inventory


# ---------------------------------------------------------------------------
# Property sampling, shared by Neo4j and Memgraph
# ---------------------------------------------------------------------------


def build_label_props_statement(labels: Sequence[str]) -> str:
    """
    ``properties(n)`` rides along with ``keys(n)`` so the sample can be *typed*:
    neither backend exposes a property's type on this path, but the value it holds
    does.
    """
    return " UNION ".join(
        f" MATCH (n:{backtick(label)}) RETURN {cypher_string(label)} as label, "
        f"keys(n) as props, properties(n) as values LIMIT 1 "
        for label in labels
    )


def build_relationship_props_statement(triples: Sequence[Triple]) -> str:
    selected = list(triples)
    if len(selected) > TRIPLE_FANOUT_LIMIT:
        selected = list({triple.name: triple for triple in selected}.values())
    return " UNION ".join(
        f" MATCH (:{backtick(triple.start)})-[r:{backtick(triple.name)}]->(:{backtick(triple.end)}) "
        f"RETURN type(r) as relationship, keys(r) as props, properties(r) as values LIMIT 1 "
        for triple in selected
    )


def parse_props_table(result: Optional[QueryData]) -> Dict[str, SampledProps]:
    """
    Both props statements return a name column, ``props`` and ``values``.

    The name column is whichever one is neither ``props`` nor ``values``, so one
    parser serves the label statement and the relationship statement alike. A name
    can repeat across branches — the relationship fan-out collapses onto one branch
    per type past ``TRIPLE_FANOUT_LIMIT`` — so names and types are merged.
    """
    out: Dict[str, SampledProps] = {}
    rows = table_rows(result) if result is not None else []
    for row in rows:
        name_column = next((column for column in row if column not in ("props", "values")), None)
        if name_column is None or "props" not in row:
            continue
        name = row.get(name_column)
        if not isinstance(name, str) or not name:
            continue
        entry = out.setdefault(name, SampledProps())
        sampled = row.get("values") if isinstance(row.get("values"), dict) else {}
        for prop in row.get("props") or []:
            prop = str(prop)
            if prop not in entry.props:
                entry.props.append(prop)
            # The first branch carrying a usable value wins, so a property that was
            # null in one sample still takes the type another sample saw.
            value_type = infer_value_type(sampled.get(prop))
            if value_type and prop not in entry.props_types:
                entry.props_types[prop] = value_type
    for entry in out.values():
        entry.props.sort()
    return out


def _procedure_type(types: Any) -> str:
    """
    The type ``db.schema.*TypeProperties()`` reports, in the contract's spelling.

    The procedures answer with a *list* — ``["Long"]``, or ``["Long", "String"]``
    for a property that has been written both ways — and name a list-valued
    property by its element type plus ``Array``. The first usable entry wins,
    which is the rule the sampling path follows too.
    """
    candidates = types if isinstance(types, (list, tuple)) else [types]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        text = candidate.strip()
        if text.upper().endswith("ARRAY"):
            return "LIST"
        normalized = normalize_type_name(text)
        if normalized:
            return normalized
    return ""


def parse_node_type_properties(result: Optional[QueryData]) -> Dict[str, SampledProps]:
    """
    ``nodeType, nodeLabels, propertyName, propertyTypes, mandatory`` -> props per label.

    ``nodeLabels`` is the *set* a node carries, so a node labelled both ``Person``
    and ``Employee`` reports its properties once under that pair; the set is
    exploded so each label receives them, the same way the sampled path does.
    A label with no properties at all still reports a row, with ``propertyName``
    null.
    """
    out: Dict[str, SampledProps] = {}
    for row in table_rows(result) if result is not None else []:
        labels = _as_labels(row.get("nodeLabels"))
        if not labels:
            continue
        name = row.get("propertyName")
        type_name = _procedure_type(row.get("propertyTypes"))
        for label in labels:
            entry = out.setdefault(label, SampledProps())
            if isinstance(name, str) and name:
                _add_prop(entry, name, type_name)
    for entry in out.values():
        entry.props.sort()
    return out


def parse_rel_type_properties(result: Optional[QueryData]) -> Dict[str, SampledProps]:
    """``relType, propertyName, propertyTypes, mandatory`` -> props per relationship type."""
    out: Dict[str, SampledProps] = {}
    for row in table_rows(result) if result is not None else []:
        rel_type = _strip_type_token(row.get("relType"))
        if not rel_type:
            continue
        name = row.get("propertyName")
        entry = out.setdefault(rel_type, SampledProps())
        if isinstance(name, str) and name:
            _add_prop(entry, name, _procedure_type(row.get("propertyTypes")))
    for entry in out.values():
        entry.props.sort()
    return out


def _strip_type_token(value: Any) -> str:
    """``":`KNOWS`"`` -> ``"KNOWS"``: the procedures quote the token, the schema does not."""
    if not isinstance(value, str):
        return ""
    return value.strip().lstrip(":").strip("`")


def build_schema(
    inventory: Inventory,
    label_props: Dict[str, SampledProps],
    relationship_props: Dict[str, SampledProps],
) -> GraphSchema:
    """
    Inventory plus sampled properties -> the contract's graph schema.

    One entry per relationship *name*, taking the first triple's endpoints: the
    contract has a single ``startCategory`` / ``endCategory`` per relationship,
    while the store may hold the same type between several label pairs.
    """
    categories = [
        _category(label, label_props.get(label, SampledProps()))
        for label in inventory.labels
    ]

    relationships: List[Relationship] = []
    seen: set = set()
    for triple in inventory.triples:
        if triple.name in seen:
            continue
        seen.add(triple.name)
        sampled = relationship_props.get(triple.name, SampledProps())
        relationships.append(
            Relationship(
                name=triple.name,
                props=list(sampled.props),
                keys=[],
                keysTypes={},
                propsTypes=dict(sampled.props_types),
                startCategory=triple.start,
                endCategory=triple.end,
            )
        )

    return GraphSchema(categories=categories, relationships=relationships)


def _category(label: str, sampled: SampledProps) -> Category:
    return Category(
        name=label,
        props=list(sampled.props),
        keys=[],
        keysTypes={},
        propsTypes=dict(sampled.props_types),
    )


async def load_neo4j_schema(run: Run) -> GraphSchema:
    """The shape from ``db.schema.visualization()``, the types from the two procedures."""
    inventory = parse_visualization(await run(VISUALIZATION_STATEMENT))
    if not inventory.labels and not inventory.triples:
        return GraphSchema()

    node_types, rel_types = await asyncio.gather(
        run(NODE_TYPE_PROPERTIES_STATEMENT),
        run(REL_TYPE_PROPERTIES_STATEMENT),
    )
    if node_types is None or rel_types is None:
        # No such procedure: an old server, or something bolt-compatible that is
        # not Neo4j. Pay for the scan rather than report a schema with no types.
        return build_schema(inventory, *(await _sample_properties(run, inventory)))

    return build_schema(
        inventory,
        parse_node_type_properties(node_types),
        parse_rel_type_properties(rel_types),
    )


async def _sample_properties(
    run: Run, inventory: Inventory
) -> Tuple[Dict[str, SampledProps], Dict[str, SampledProps]]:
    """
    Properties are a bonus on top of the inventory: a store that refuses these
    still yields categories and relationship types, which is what the panels need
    to render at all.
    """


    async def sample(statement: Optional[str]) -> Dict[str, SampledProps]:
        # Nothing to ask about is not a failure; it is an empty answer.
        return parse_props_table(await run(statement)) if statement else {}

    label_props, relationship_props = await asyncio.gather(
        sample(build_label_props_statement(inventory.labels) if inventory.labels else None),
        sample(build_relationship_props_statement(inventory.triples) if inventory.triples else None),
    )
    return label_props, relationship_props


# ---------------------------------------------------------------------------
# Memgraph
# ---------------------------------------------------------------------------


async def load_memgraph_schema(run: Run) -> GraphSchema:
    """``SHOW SCHEMA INFO`` when the server offers it; the sampling probe otherwise."""
    declared = parse_schema_info(await run(SHOW_SCHEMA_INFO_STATEMENT))
    if declared is not None:
        return declared

    inventory = parse_inventory(
        *await asyncio.gather(run(NODE_INVENTORY_STATEMENT), run(EDGE_INVENTORY_STATEMENT))
    )
    if not inventory.labels and not inventory.triples:
        return GraphSchema()
    return build_schema(inventory, *(await _sample_properties(run, inventory)))


def parse_inventory(node_result: Optional[QueryData], edge_result: Optional[QueryData]) -> Inventory:
    """The label sets and edge triples the store actually holds, exploded per label."""
    inventory = Inventory()

    for row in table_rows(node_result) if node_result is not None else []:
        for label in _as_labels(row.get("labels")):
            if label not in inventory.labels:
                inventory.labels.append(label)

    seen: set = set()
    for row in table_rows(edge_result) if edge_result is not None else []:
        name = row.get("relName")
        if not isinstance(name, str) or not name:
            continue
        for start in _as_labels(row.get("startL")):
            for end in _as_labels(row.get("endL")):
                triple = Triple(name=name, start=start, end=end)
                if triple in seen:
                    continue
                seen.add(triple)
                inventory.triples.append(triple)

    return inventory


def parse_schema_info(result: Optional[QueryData]) -> Optional[GraphSchema]:
    """
    The JSON document ``SHOW SCHEMA INFO`` returns.

    ``None`` means "fall through to the next route" — the statement did not produce
    a usable document, or produced an empty one. Neither means "this database has
    no schema".
    """
    document = _schema_info_document(result)
    if document is None:
        return None

    builder = _SchemaBuilder()
    for node in document.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        labels = _as_labels(node.get("labels"))
        builder.add_node(labels)
        for prop in node.get("properties") or []:
            if isinstance(prop, dict):
                builder.add_node(labels, str(prop.get("key") or ""), _dominant_type(prop.get("types")))

    for edge in document.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        name = str(edge.get("type") or "")
        start = _as_labels(edge.get("start_node_labels"))
        end = _as_labels(edge.get("end_node_labels"))
        builder.add_edge(name, start, end)
        for prop in edge.get("properties") or []:
            if isinstance(prop, dict):
                builder.add_edge(
                    name, start, end, str(prop.get("key") or ""), _dominant_type(prop.get("types"))
                )

    return None if builder.is_empty() else builder.build()


def _schema_info_document(result: Optional[QueryData]) -> Optional[Dict[str, Any]]:
    """The single cell the statement returns, parsed if the server sent it as text."""
    if result is None or result.type != "TABLE" or not isinstance(result.data, list):
        return None
    for row in result.data[1:]:
        for cell in row if isinstance(row, (list, tuple)) else [row]:
            value = cell
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict) and (
                isinstance(value.get("nodes"), list) or isinstance(value.get("edges"), list)
            ):
                return value
    return None


def _dominant_type(types: Any) -> str:
    """``SHOW SCHEMA INFO`` reports every type a property was seen with, plus counts."""
    best = ""
    best_count = -1
    for entry in types or []:
        if not isinstance(entry, dict):
            continue
        count = entry.get("count") if isinstance(entry.get("count"), int) else 0
        name = normalize_type_name(entry.get("type"))
        if name and count > best_count:
            best = name
            best_count = count
    return best


def _as_labels(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if isinstance(item, str) and item]
    return [value] if isinstance(value, str) and value else []


class _SchemaBuilder:
    """Collects labels, label properties and edge triples, then emits a GraphSchema."""

    def __init__(self) -> None:
        self._categories: Dict[str, SampledProps] = {}
        self._triples: Dict[Triple, SampledProps] = {}

    def add_node(self, labels: Sequence[str], prop: str = "", type_name: str = "") -> None:
        for label in labels:
            if not label:
                continue
            _add_prop(self._categories.setdefault(label, SampledProps()), prop, type_name)

    def add_edge(
        self,
        name: str,
        start_labels: Sequence[str],
        end_labels: Sequence[str],
        prop: str = "",
        type_name: str = "",
    ) -> None:
        if not name:
            return
        for start in start_labels:
            for end in end_labels:
                if not start or not end:
                    continue
                triple = Triple(name=name, start=start, end=end)
                _add_prop(self._triples.setdefault(triple, SampledProps()), prop, type_name)

    def is_empty(self) -> bool:
        return not self._categories and not self._triples

    def build(self) -> GraphSchema:
        inventory = Inventory(labels=list(self._categories.keys()), triples=list(self._triples.keys()))
        relationship_props: Dict[str, SampledProps] = {}
        for triple, sampled in self._triples.items():
            relationship_props.setdefault(triple.name, sampled)
        return build_schema(inventory, self._categories, relationship_props)


def _add_prop(bag: SampledProps, name: str, type_name: str) -> None:
    if not name:
        return
    if name not in bag.props:
        bag.props.append(name)
    normalized = normalize_type_name(type_name)
    # First observed type wins, so a property that is sometimes null still reports
    # its real type rather than "NULL".
    if normalized and name not in bag.props_types:
        bag.props_types[name] = normalized
