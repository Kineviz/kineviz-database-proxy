# -*- coding: utf-8 -*-
"""
The reads this driver writes for itself.

``compile.py`` builds the statements a *query* asks for. These three are not queries: a
sample, a direction preview and the row estimates behind them are the driver's own
bounded reads, and each one states the same rule — declare the table from the mapping,
project only the columns the mapping named, and put the ``LIMIT`` in the statement,
because a full scan of a table someone is only looking at is not a mistake the proxy gets
to make on a customer's server.

Nothing here reflects at query time. The columns are the ones the mapping declares, with
their declared types, so a parameter is bound as the column's own type; that is the
``column_type`` seam's reason to exist, and a read that reflected first would both cost a
round trip and bind the wrong type.

A **direction preview** is the reason this module is not simply "sample rows". A link
whose ends are swapped answers nothing rather than an error: ``1001 -> 2001`` cannot read
as wrong, so the only way to tell a person their edge points backwards is to run the join
both ways against their real data and say which way round it went.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import MetaData, Table
from sqlalchemy import Column as SAColumn
from sqlalchemy import select

from .engine import EngineSpelling
from .mapping import EffectiveMapping
from .schemas import NodeSchema
from .sql_pipeline import PIPELINE, PipelineMissing

#: Rows one preview direction may return. Past this it is an export, and an export is a
#: query the person should have to write.
PREVIEW_ROWS_MAX = 100

#: The two readings of one link: as the mapping declares it, and with its ends swapped.
DIRECTION_DECLARED = "declared"
DIRECTION_FLIPPED = "flipped"

#: The closed set a direction check can land in. ``settled`` is the happy answer, and
#: ``backwards`` is the one this page exists to catch; ``neither`` says the table has rows
#: and nothing joins, which is never reported as a success. ``you-decide`` is the two
#: directions both joining rows, which the database cannot settle and a person can.
#: The hyphen is safe on the wire: the page maps '_' and '-' to a space and matches
#: case-insensitively, so it reads either spelling, and the spec spells it with a hyphen.
VERDICTS = ("settled", "backwards", "you-decide", "neither")


class RelationalProblem(ValueError):
    """
    A request this proxy can answer only by naming what is wrong with it.

    Carries a path so an endpoint can report ``{"path", "message"}`` — the same shape a
    rejected mapping upload uses, because a person fixing a draft should not have to learn
    two error formats from one page.
    """

    def __init__(self, path: str, message: str) -> None:
        super().__init__("{}: {}".format(path, message))
        self.path = path
        self.message = message

    @property
    def problems(self) -> List[Dict[str, str]]:
        return [{"path": self.path, "message": self.message}]


def plain_cell(value: Any) -> Any:
    """
    One cell of the driver's own reads, made safe for the wire.

    The rule is ``convert.json_safe``'s — nothing FastAPI cannot serialise leaves the
    driver, a ``Decimal`` keeps its digits when reading it back would lose them, bytes go
    out as base64 — so it is delegated rather than restated. The fallback only runs on a
    build whose compile stage is absent, and it is deliberately narrower: a string, a
    number, a boolean and nothing else, because an unexpected type arriving on the wire is
    a 500 on someone's schema panel.
    """
    try:
        return PIPELINE.json_safe(value)
    except PipelineMissing:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        iso = getattr(value, "isoformat", None)
        if callable(iso):
            try:
                return str(iso())
            except Exception:
                pass
        return str(value)[:512]


def display_column(node: NodeSchema) -> str:
    """
    The column a preview row shows for this record.

    ``show_as`` when the label has one, its key otherwise: a preview that printed two
    integers a person cannot recognise says nothing about whether the link is right.
    """
    return node.show_as or node.key


def table_of(
    spelling: EngineSpelling,
    name: str,
    columns: Mapping[str, Optional[str]],
    schema: Optional[str],
) -> Table:
    """
    A table declared from the mapping, in a ``MetaData`` of its own.

    One per statement, the way ``compile`` does it, because two labels of one physical
    table — a direct foreign key *is* the record table — would otherwise collide in the
    same ``MetaData`` and be silently merged into one set of columns.
    """
    meta = MetaData()
    return Table(
        name,
        meta,
        *[SAColumn(str(column), spelling.declared_column_type(declared)) for column, declared in columns.items()],
        schema=schema,
    )


def _aliased(spelling: EngineSpelling, node: NodeSchema, name: str, schema: Optional[str]) -> Any:
    """One record's table, aliased, carrying only the columns a preview row can show."""
    columns: Dict[str, Optional[str]] = {node.key: node.key_type}
    shown = display_column(node)
    if shown != node.key:
        columns.setdefault(shown, node.props.get(shown))
    return table_of(spelling, node.table, columns, node.schema or schema).alias(name)


def _end(raw: Any, path: str) -> Dict[str, Any]:
    """One endpoint of a preview pair, insisting on the edge column it joins on."""
    if not isinstance(raw, Mapping):
        raise RelationalProblem(path, "an endpoint of this pair is missing")
    if not raw.get("column"):
        raise RelationalProblem(path, "an endpoint names no column of the link table")
    return dict(raw)


def _resolve_end(
    mapping: EffectiveMapping, end: Dict[str, Any], table: str, path: str
) -> NodeSchema:
    """
    The record one end points at: by its stated table and key, else by its label.

    A draft from the editor may name a label nothing has saved yet, so an endpoint may
    carry its own ``table`` and ``key``; a saved link never needs to. When neither works
    the refusal names the label, because "that is not a record type" is the sentence a
    person can act on and an empty result is not.
    """
    stated_table = end.get("table")
    stated_key = end.get("key")
    if stated_table and stated_key:
        return NodeSchema(
            label=str(end.get("label") or stated_table),
            table=str(stated_table),
            key=str(stated_key),
            key_type=str(end.get("key_type") or ""),
            props={},
            schema=end.get("schema"),
            show_as=end.get("show_as"),
        )
    label = str(end.get("label") or "")
    found = mapping.nodes.get(label)
    if found is not None:
        return found
    # A direct foreign key is an edge whose table *is* the record table, so a pair whose
    # labels are not in the mapping yet can still be previewed against that table.
    if table in {node.table for node in mapping.nodes.values()}:
        return next(node for node in mapping.nodes.values() if node.table == table)
    raise RelationalProblem(
        path,
        "{} is not a record type in this mapping: give the pair a label the mapping has, "
        "or name its table and key".format(label or "this endpoint"),
    )


class RelationalReads:
    """
    The bounded reads. ``self.spelling``, ``self.config`` and ``self.access`` are required.

    Mixed into ``RelationalDriver``, which is where the connection lives; nothing here
    opens one, so every method is a statement builder and the driver does the awaiting.
    """

    spelling: EngineSpelling
    config: Any
    access: Any

    def sample_statement(self, node: NodeSchema, limit: int) -> Any:
        """
        One label's mapped columns, ``LIMIT`` at the source.

        Only the mapping's columns are projected: a blob left out of the mapping is not
        fetched in order to be thrown away, and the columns a sample shows are the columns
        GraphXR will be able to show for that node.
        """
        columns: Dict[str, Optional[str]] = dict(node.all_props())
        if node.show_as:
            columns.setdefault(node.show_as, node.props.get(node.show_as))
        table = table_of(self.spelling, node.table, columns, node.schema or self.access.schema)
        statement = select(*[table.c[name] for name in columns]).select_from(table).limit(limit)
        return self._guarded(statement)

    def preview_statement(
        self,
        mapping: EffectiveMapping,
        table: str,
        pair: Mapping[str, Any],
        limit: int,
        direction: str,
    ) -> Any:
        """
        One direction of one link: the same two edge columns, the two records swapped.

        ``flipped`` is not a second reading of a row. It is the other claim about which
        record each column holds, which is the only thing that can be wrong about a link
        once the columns are known. Both ends are aliased rather than named, so a
        self-referencing table joins to itself without either side becoming ambiguous.
        """
        src = _end(pair.get("src"), "{}.src".format(table))
        dst = _end(pair.get("dst"), "{}.dst".format(table))
        src_node = _resolve_end(mapping, src, table, "{}.src".format(table))
        dst_node = _resolve_end(mapping, dst, table, "{}.dst".format(table))
        near = str(src["column"])
        far = str(dst["column"])
        if direction == DIRECTION_FLIPPED:
            near, far = far, near
        edge = table_of(
            self.spelling,
            table,
            {near: src.get("column_type"), far: dst.get("column_type")},
            src_node.schema or self.access.schema,
        )
        tail = _aliased(self.spelling, src_node, "gx_src", self.access.schema)
        head = _aliased(self.spelling, dst_node, "gx_dst", self.access.schema)
        statement = (
            select(
                # ``from``/``to`` are the two keys and ``*_label`` the values a person can
                # read: ``1001 -> 2001`` cannot be seen to be wrong, ``Avery Chen -> Northwind
                # Logistics`` can. Both are named for the *reading*, not for the column, so the
                # flipped direction comes back with its arrow the other way round.
                tail.c[display_column(src_node)].label("from_label"),
                head.c[display_column(dst_node)].label("to_label"),
                edge.c[near].label("from"),
                edge.c[far].label("to"),
            )
            .select_from(edge.join(tail, edge.c[near] == tail.c[src_node.key]).join(
                head, edge.c[far] == head.c[dst_node.key]
            ))
            .limit(limit)
        )
        return self._guarded(statement)

    def _guarded(self, statement: Any) -> Any:
        """The engine's own read guard, when it has a per-statement one."""
        hint = self.spelling.timeout_hint(self.config)
        return statement.prefix_with(hint, dialect=self.spelling.name) if hint else statement


def verdict_of(directions: Sequence[Mapping[str, Any]]) -> str:
    """
    Which way round this link goes, from what the two directions actually returned.

    Empty on both is ``neither``: the table has rows and nothing joins, so the columns
    named are not the two keys. It is never reported as ``settled``, which would put a
    full canvas of nothing on the screen and let a person conclude their link is fine.
    """
    states = {
        str(found.get("direction")): int(found.get("count") or 0) > 0 for found in directions
    }
    declared = states.get(DIRECTION_DECLARED, False)
    flipped = states.get(DIRECTION_FLIPPED, False)
    if declared and flipped:
        return "you-decide"
    if declared:
        return "settled"
    if flipped:
        return "backwards"
    return "neither"
