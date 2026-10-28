# -*- coding: utf-8 -*-
"""
The graph schema for LatticeDB, which has no catalog to read it from.

Every other backend here can be asked. Kuzu and Ladybug answer ``CALL
show_tables()`` and ``table_info()`` with declared columns and a marked primary
key; Neo4j and Memgraph have schema procedures; the GQL backends have a graph
definition. LatticeDB has none of that, and not by omission -- it has no schema at
all. A node is a bag of labels and a map of properties, created without declaring
either, so there is nothing to look up and the only evidence is the data.

So the schema is **inferred**, in two shapes of question:

  - **What exists.** ``RETURN DISTINCT labels(n)`` and ``RETURN DISTINCT labels(n),
    type(r), labels(m)``. These scan inside the engine but answer with one row per
    distinct combination, so what crosses the wire is the shape of the graph rather
    than the graph.
  - **What is on it.** ``MATCH (n:Person) RETURN properties(n) ... LIMIT n``, once
    per category and once per relationship type. Properties are sampled because
    two nodes sharing a label need not share a property, and types come from the
    values because no value here carries a declared one.

Both are paged at the source and both are capped: a store with a thousand distinct
labels answers with the first ``MAX_CATEGORIES`` of them rather than a thousand
round-trips. Sampling is why ``props`` is a *description* and not a guarantee --
a property that appears only on the ten-thousandth node will not be in it.

A node may carry several labels, and each one is reported as its own category: a
``["Person", "Employee"]`` node is evidence about both. It may also carry none,
which is not an error and simply belongs to no category.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ...models.project import Category, GraphSchema, Relationship
from ..bolt_mapping import infer_value_type
from ..dialect import LATTICEDB_DIALECT, cypher_string

#: Distinct label sets, and distinct endpoint/type triples, to ask for at most.
#: Far above any real graph's variety, and low enough to bound a pathological one.
MAX_CATEGORIES = 200
MAX_RELATIONSHIPS = 200

#: Nodes or edges to read properties from, per category or type.
SAMPLE_ROWS = 100

#: A row reader: one statement in, its rows as dicts keyed by column name out.
RowReader = Callable[[str], Awaitable[Optional[List[Dict[str, Any]]]]]


def node_match(variable: str, label: str) -> str:
    """
    ``MATCH`` for one label, written the only way this label can be written.

    LatticeDB has no quoted-identifier syntax, so a label that is not a bare
    identifier cannot go in a pattern at all and is matched by predicate instead.
    The rule lives in the dialect so there is one answer to it, not two.
    """
    if LATTICEDB_DIALECT.pattern_identifier_ok(label):
        return f"MATCH ({variable}:{label})"
    return f"MATCH ({variable}) WHERE {cypher_string(label)} IN labels({variable})"


def relationship_match(label: str) -> str:
    """``MATCH`` for one relationship type, by pattern or by predicate for the same reason."""
    if LATTICEDB_DIALECT.pattern_identifier_ok(label):
        return f"MATCH (n)-[r:{label}]->(m)"
    return f"MATCH (n)-[r]->(m) WHERE type(r) IN [{cypher_string(label)}]"


def distinct_labels_statement(limit: int = MAX_CATEGORIES) -> str:
    return f"MATCH (n) RETURN DISTINCT labels(n) SKIP 0 LIMIT {limit}"


def distinct_relationships_statement(limit: int = MAX_RELATIONSHIPS) -> str:
    return (
        "MATCH (n)-[r]->(m) RETURN DISTINCT labels(n), type(r), labels(m) "
        f"SKIP 0 LIMIT {limit}"
    )


def node_sample_statement(label: str, limit: int = SAMPLE_ROWS) -> str:
    return f"{node_match('n', label)} RETURN properties(n) SKIP 0 LIMIT {limit}"


def relationship_sample_statement(label: str, limit: int = SAMPLE_ROWS) -> str:
    return f"{relationship_match(label)} RETURN properties(r) SKIP 0 LIMIT {limit}"


def _label_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return [str(value)] if value is not None else []


def category_names(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """
    Every label named by a ``DISTINCT labels(n)`` result, de-duplicated in order.

    The rows are label *sets*, so ``["Person", "Employee"]`` and ``["Person"]``
    together name two categories, not three.
    """
    names: Dict[str, None] = {}
    for row in rows or ():
        for label in _label_list(row.get("labels(n)")):
            names.setdefault(label, None)
    return list(names.keys())


def property_shape(rows: Sequence[Dict[str, Any]], column: str) -> Dict[str, str]:
    """
    Property name -> type, merged over a sample.

    First non-empty type wins, so a property that is null on the first node and a
    string on the second is a string rather than untyped. A property seen only as
    null keeps an empty type: it exists, and nothing yet says what it holds.
    """
    types: Dict[str, str] = {}
    for row in rows or ():
        properties = row.get(column)
        if not isinstance(properties, dict):
            continue
        for name, value in properties.items():
            key = str(name)
            existing = types.get(key)
            if existing:
                continue
            types[key] = infer_value_type(value)
    return types


def build_category(name: str, sample: Sequence[Dict[str, Any]]) -> Category:
    """
    One label's sampled properties, as a category.

    ``keys`` is left empty on purpose. LatticeDB has no primary key to report, and
    it needs none: its identity is ``internal-id``, so ``/expand`` re-selects a
    seed by ``id(n)`` and never has to know which property would identify it.
    """
    types = property_shape(sample, "properties(n)")
    return Category(
        name=name,
        props=list(types.keys()),
        keys=[],
        keysTypes={},
        propsTypes=dict(types),
    )


def build_relationship(
    name: str,
    start_category: str,
    end_category: str,
    sample: Sequence[Dict[str, Any]],
) -> Relationship:
    types = property_shape(sample, "properties(r)")
    return Relationship(
        name=name,
        props=list(types.keys()),
        keys=[],
        keysTypes={},
        propsTypes=dict(types),
        startCategory=start_category,
        endCategory=end_category,
    )


def relationship_triples(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    ``(startCategory, name, endCategory)`` per distinct triple, first-seen order.

    A multi-label endpoint is reported by its first label, because a relationship
    definition names one category at each end. The whole label set is still on the
    node itself, where the client can see it.
    """
    seen: Dict[tuple, Dict[str, str]] = {}
    for row in rows or ():
        name = row.get("type(r)")
        if not name:
            continue
        starts = _label_list(row.get("labels(n)"))
        ends = _label_list(row.get("labels(m)"))
        triple = (starts[0] if starts else "", str(name), ends[0] if ends else "")
        seen.setdefault(triple, {"start": triple[0], "name": triple[1], "end": triple[2]})
    return list(seen.values())


async def load_lattice_schema(
    read: RowReader,
    *,
    max_categories: int = MAX_CATEGORIES,
    max_relationships: int = MAX_RELATIONSHIPS,
    sample_rows: int = SAMPLE_ROWS,
) -> GraphSchema:
    """
    The whole schema, in ``2 + categories + relationship types`` statements.

    ``read`` returns None for a statement the engine refused, which is an answer
    rather than a fault: a store with no relationships at all still has categories
    worth reporting, and one sampling statement failing must not empty the schema.
    """
    label_rows = await read(distinct_labels_statement(max_categories)) or []
    names = category_names(label_rows)[:max_categories]

    categories: List[Category] = []
    for name in names:
        sample = await read(node_sample_statement(name, sample_rows)) or []
        categories.append(build_category(name, sample))

    triple_rows = await read(distinct_relationships_statement(max_relationships)) or []
    triples = relationship_triples(triple_rows)[:max_relationships]

    #: One sample per *type*, not per triple: the same type between two different
    #: pairs of categories carries the same properties, and sampling it twice would
    #: buy nothing but a round-trip.
    samples: Dict[str, List[Dict[str, Any]]] = {}
    relationships: List[Relationship] = []
    for triple in triples:
        name = triple["name"]
        if name not in samples:
            samples[name] = await read(relationship_sample_statement(name, sample_rows)) or []
        relationships.append(
            build_relationship(name, triple["start"], triple["end"], samples[name])
        )

    return GraphSchema(categories=categories, relationships=relationships)
