# -*- coding: utf-8 -*-
"""
The mapping, rendered as the contract's schema documents.

What ``/graphSchema`` answers, and the type vocabulary it answers in. Kept off
``driver.py`` because it is the whole of that endpoint's job — one mapping in, one
``GraphSchema`` out — and because a driver file that carries both the query path and
the schema shape is the kind of file every later lane has to re-read.

The rule that makes any of this safe is that a ``Category`` always carries a non-empty
``keys``: a ``primary-key`` backend turns a seed id into a predicate by asking which
property is that label's key, ``intents._resolve_refs`` drops a seed whose label has
none, and an empty ``keys`` therefore shows up as every ``/expand`` answering an empty
graph. That is the difference between a schema surface that lies and one that fails.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...models.project import Category, GraphSchema, Relationship
from .engine import PROPERTY_TYPES
from .mapping import EffectiveMapping
from .schemas import NodeSchema, RelSchema

#: A type nobody could name. It is quoted by ``dialect.render_key_literal``, which is the
#: safe direction to be wrong in: a numeric key that arrives as text still matches an
#: indexed column on all three engines, a text key read as numeric does not match at all.
UNKNOWN_TYPE = "STRING"


def problems_text(problems: Sequence[Tuple[str, str]]) -> str:
    """Path-named problems as one sentence, because a mapping that will not load says where."""
    return "; ".join("{}: {}".format(path, message) for path, message in problems)


class RelationalSchemaSurface:
    """
    ``self.spelling`` is required; ``RelationalDriver`` provides it.

    Nothing here reaches a database: the input is an ``EffectiveMapping``, which is where
    discovery and a person's override have already been reconciled.
    """

    spelling: Any

    def normalise(self, declared: Optional[str]) -> str:
        """
        A column's declared type as one of the shared ``PROPERTY_TYPES``.

        A name already in the vocabulary passes through untouched. The mapping stores
        what discovery read, which is the engine's own spelling (``bigint``,
        ``varchar(50)``), but a person writing a file by hand may well write ``INT64``:
        feeding that to ``normalise_type`` asks the engine table for a type it never
        declared and gets ``STRING`` back — which would then quote a numeric key and turn
        an index lookup into a scan. ``dialect._NUMERIC_KEY_TYPES`` is compared
        lowercased, so the pass-through is also what keeps that table honest.
        """
        text = str(declared or "").strip()
        if not text:
            return UNKNOWN_TYPE
        if text.upper() in PROPERTY_TYPES:
            return text.upper()
        try:
            return str(self.spelling.normalise_type(text))
        except Exception:  # a spelling that cannot classify its own column type
            return UNKNOWN_TYPE

    def category_of(self, node: NodeSchema) -> Category:
        """
        One label, with its key non-negotiable.

        The key is listed first in ``props`` because that is how ``NodeSchema.all_props``
        orders it and how a canvas shows a record: what identifies it, then what it holds.
        """
        declared = node.all_props()
        types = {name: self.normalise(type_name) for name, type_name in declared.items()}
        key_type = types.get(node.key) or UNKNOWN_TYPE
        return Category(
            name=node.label,
            props=list(types.keys()),
            keys=[node.key],
            keysTypes={node.key: key_type},
            propsTypes=types,
        )

    def relationship_of(self, edge: RelSchema) -> Optional[Relationship]:
        """
        One edge type, drawn from its largest pair.

        The contract allows a single ``startCategory``/``endCategory`` per relationship, so
        a polymorphic link shows the pair most likely to be the one a person means — and
        the other pairs still answer at query time, because the plan enumerates every
        candidate rather than reading the schema panel.

        An edge that names no record is dropped rather than drawn with a blank end: a
        relationship from nothing to nothing is a canvas entry that can never be clicked,
        and ``mapping`` has already put the reason in ``warnings``.
        """
        pair = edge.representative
        if not pair.src.label or not pair.dst.label:
            return None
        types = {name: self.normalise(type_name) for name, type_name in edge.props.items()}
        keys: List[str] = []
        keys_types: Dict[str, str] = {}
        if edge.key:
            keys = [edge.key]
            keys_types = {edge.key: self.normalise(edge.key_type)}
        return Relationship(
            name=edge.type,
            props=list(types.keys()),
            keys=keys,
            keysTypes=keys_types,
            propsTypes=types,
            startCategory=pair.src.label,
            endCategory=pair.dst.label,
        )

    def graph_schema(self, mapping: EffectiveMapping) -> GraphSchema:
        """Every label and every link type this mapping serves, as the contract's document."""
        categories = [self.category_of(node) for node in mapping.nodes.values()]
        relationships = [
            built
            for built in (self.relationship_of(edge) for edge in mapping.edges.values())
            if built is not None
        ]
        return GraphSchema(categories=categories, relationships=relationships)

    def mapping_categories(self, mapping: EffectiveMapping) -> Dict[str, Any]:
        """
        The category documents keyed by label, in the shape ``GraphIntentSupport`` wants.

        Handed to ``remember_graph_categories`` the moment a mapping resolves, so the
        intent layer never re-probes for a seed's key and a ``/query`` that touched the
        mapping is enough to make a following ``/expand`` on the same driver work.
        """
        return {label: self.category_of(node).model_dump() for label, node in mapping.nodes.items()}
