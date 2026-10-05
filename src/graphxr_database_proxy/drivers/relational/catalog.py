"""
Discovery: read a live schema and propose what it means as a graph.

Two halves, deliberately split.

``discover`` only **reads**. It asks an inspector for table names, then for columns,
primary keys and foreign keys one *page of table names* at a time, and stops there.
A 3 000-table ERP schema is not reflected in one call, and pulling every column of
every table to answer a page of names is the mistake the repo rules name.

``propose`` only **judges**. It turns a catalog into the draft override document and
writes the reasoning for each guess into ``notes`` — "both ends point at Account and
nothing points back at it" — because a draft a person cannot disagree with is not
reviewable, it is simply accepted.

No sqlalchemy, no DBAPI: the inspector is duck-typed to the four methods this needs,
so the whole module runs against a fake object with no server, and the row estimates
arrive through an injected callable for the same reason — another lane knows what
``sys.partitions`` looks like and this one must not.

One rule everything below follows: **an endpoint's column is a column on the edge
table.** For a junction both ends are its two foreign-key columns; for a direct
foreign key the edge table is the child record itself, so the src end is the child's
own key and the dst end is the foreign key column. The node side of every join comes
from ``NodeSchema.key``, which is why a reflected parent column is never needed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from ...models.graph_mapping import (
    GraphMapping,
    MappingEdge,
    MappingEndpoint,
    MappingNode,
    MappingPair,
    UNKNOWN_TYPE,
)
from .reflect import (
    PAGE_SIZE,
    Inspector,
    Row,
    RowCounts,
    _bare,
    _probe_names,
    _read_pages,
    type_name,
)

#: Caps on what becomes a graph. A 3 000-table schema is not a 3 000-label graph: the
#: panel becomes unusable and the first expand fans out across all of it. The fix is
#: ``include`` / ``exclude``, so a cap names which half was too big instead of quietly
#: serving half of it.
MAX_LABELS = 200
MAX_EDGES = 400
MAX_PROPS = 200

#: Schemas that are the engine talking to itself.
INTERNAL_SCHEMAS = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "pg_toast",
        "pg_temp",
        "sys",
        "mysql",
        "performance_schema",
        "guest",
    }
)

#: Names that are never anybody's data: mssql temp (``#``) and rebuild artifacts,
#: postgres catalog, mysql's InnoDB dictionary, Management Studio's diagram tables.
SYSTEM_TABLE_PREFIXES = ("#", "pg_", "mysql_", "innodb_", "sqladmin_", "msdb_")
SYSTEM_TABLE_NAMES = ("sysdiagrams", "spt_values")

@dataclass(frozen=True)
class ForeignKey:
    """One foreign key, flattened to what a graph edge needs."""

    name: str
    columns: Tuple[str, ...]
    ref_table: str
    ref_schema: Optional[str] = None
    ref_columns: Tuple[str, ...] = ()

    @property
    def single(self) -> bool:
        """One column: the only shape a link can be drawn from."""
        return len(self.columns) == 1


@dataclass
class SchemaTable:
    """One table as the catalog read it; the judgement is ``key_candidate``."""

    name: str
    schema: Optional[str] = None
    columns: Dict[str, str] = field(default_factory=dict)
    not_null: Set[str] = field(default_factory=set)
    pk_name: Optional[str] = None
    pk_columns: Tuple[str, ...] = ()
    uniques: Tuple[str, ...] = ()
    foreign_keys: Tuple[ForeignKey, ...] = ()
    referenced: bool = False
    #: Rows the injected statistics named for this table, or ``None`` when they did not
    #: name it. Unknown is not a zero: it is what the caps rank on, so a log whose
    #: statistics were never gathered cannot sort below a table that is really empty.
    row_estimate: Optional[int] = None

    def key_candidate(self) -> Optional[str]:
        """
        The one column a graph identity can be built on: a single-column primary key,
        else a single-column NOT NULL unique, else ``None``.

        ``None`` means this table cannot be a node, and ``propose`` records the reason
        instead of inventing a key out of the first column it likes.
        """
        if len(self.pk_columns) == 1:
            return self.pk_columns[0]
        return self.widest_unique()

    def widest_unique(self) -> Optional[str]:
        """The broadest single-column NOT NULL unique, so a key cannot truncate."""
        ranked = sorted(self.uniques, key=lambda name: -_type_width(self.columns.get(name, "")))
        for name in ranked:
            if name in self.not_null:
                return name
        return None

    def columns_except(self, drop: Iterable[str]) -> Dict[str, str]:
        gone = set(drop)
        return {name: t for name, t in self.columns.items() if name not in gone}

    def is_link_table(self) -> bool:
        """
        Exactly two foreign keys, nothing points back at it, and both ends are single.

        The third clause is the spec's "a schema cannot tell you the direction" kept
        honest: a junction whose key is a composite foreign key is still a junction,
        but the contract matches one column per end, so it is read as a record.
        """
        return (
            len(self.foreign_keys) == 2
            and not self.referenced
            and all(fk.single for fk in self.foreign_keys)
        )


@dataclass
class Catalog:
    """What was read, what could not be used, and what was never asked about."""

    schema: Optional[str] = None
    tables: List[SchemaTable] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def by_name(self) -> Dict[str, SchemaTable]:
        return {table.name: table for table in self.tables}

    def has_table(self, name: str) -> bool:
        """Whether this name was read as a table at all (a view is not, and cannot be an end)."""
        return name in self.by_name


def discover(
    inspector: Inspector,
    schema: Optional[str] = None,
    row_counts: Optional[RowCounts] = None,
    page_size: int = PAGE_SIZE,
    internal_schemas: Iterable[str] = INTERNAL_SCHEMAS,
) -> Catalog:
    """
    Read a schema: names first, then columns/PKs/FKs a page of names at a time.

    Views and engine internals are dropped here rather than carried and filtered
    later, because carrying them means every rule downstream has to remember to.
    """
    if _is_internal(schema, internal_schemas):
        return Catalog(
            schema=schema,
            warnings=["schema {!r} is the engine's own; nothing in it is your data".format(schema)],
        )
    catalog = Catalog(schema=schema)
    names = _visible_names(inspector, schema, catalog, internal_schemas)
    pages = _read_pages(inspector, names, schema, page_size, row_counts)
    _build_tables(catalog, names, pages)
    return catalog


def propose(
    source: Union[Inspector, Catalog],
    schema: Optional[str] = None,
    row_counts: Optional[RowCounts] = None,
    page_size: int = PAGE_SIZE,
) -> GraphMapping:
    """
    The draft override document for a schema.

    Pass an inspector, or a ``Catalog`` already read — and pass the ``Catalog`` when
    you want the caps and skips, because they are appended to ``catalog.warnings`` and
    a document has nowhere else to put them.
    """
    catalog = (
        source
        if isinstance(source, Catalog)
        else discover(source, schema=schema, row_counts=row_counts, page_size=page_size)
    )
    return _Proposal(catalog).document()


# ---------------------------------------------------------------------- reading

def _is_internal(schema: Optional[str], internal_schemas: Iterable[str]) -> bool:
    if not schema:
        return False
    return _bare(schema).casefold() in {str(one).casefold() for one in internal_schemas}


def _visible_names(
    inspector: Inspector,
    schema: Optional[str],
    catalog: Catalog,
    internal_schemas: Iterable[str],
) -> List[str]:
    """The tables the inspector calls *tables*, minus anything the engine keeps for itself."""
    names = [str(name) for name in inspector.get_table_names(schema=schema)]
    views = set(_probe_names(inspector, "get_view_names", schema=schema))
    internal = {str(one).casefold() for one in internal_schemas}
    keep: List[str] = []
    for name in names:
        if name in views:
            catalog.skipped.append(name)
            continue
        if _is_system_table(name) or name.casefold() in internal:
            catalog.skipped.append(name)
            continue
        keep.append(name)
    return keep


def _is_system_table(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith(SYSTEM_TABLE_PREFIXES) or lowered in SYSTEM_TABLE_NAMES


def _build_tables(catalog: Catalog, names: Sequence[str], pages: Dict[str, Dict[str, Any]]) -> None:
    """Turn the reflected pages into ``SchemaTable`` objects, and mark who is referenced."""
    referenced: Set[str] = set()
    foreign: Dict[str, List[ForeignKey]] = {}
    for name in names:
        keys = []
        for row in pages["fk"].get(name) or []:
            fk = _foreign_key(row)
            if fk is not None:
                keys.append(fk)
                referenced.add(fk.ref_table)
        foreign[name] = keys
    for name in names:
        rows = pages["columns"].get(name) or []
        columns: Dict[str, str] = {}
        not_null: Set[str] = set()
        for row in rows:
            column = str((row or {}).get("name") or "")
            if not column:
                continue
            columns[column] = type_name(row.get("type"))
            if not row.get("nullable", True):
                not_null.add(column)
        if not columns:
            # The assumption this rests on: a real table in mssql, postgresql or mysql
            # always has at least one column, so "nothing came back" can only mean "no
            # such table". It matters because a caller may name the tables it wants, and
            # a name it invented would otherwise be built into an empty entry and answered
            # as a table the engine never had.
            continue
        pk = pages["pk"].get(name) or {}
        pk_columns = tuple(str(one) for one in (pk.get("constrained_columns") or ()))
        catalog.tables.append(
            SchemaTable(
                name=name,
                schema=catalog.schema,
                columns=columns,
                not_null=not_null,
                pk_name=pk.get("constraint_name"),
                pk_columns=pk_columns,
                uniques=tuple(pages["unique"].get(name) or ()),
                foreign_keys=tuple(foreign[name]),
                referenced=name in referenced,
                row_estimate=pages["rows"].get(name),
            )
        )


def _foreign_key(row: Row) -> Optional[ForeignKey]:
    cols = tuple(str(one) for one in (row.get("constrained_columns") or ()))
    parent = str(row.get("referred_table") or "")
    if not cols or not parent:
        return None
    return ForeignKey(
        name=str(row.get("name") or ""),
        columns=cols,
        ref_table=parent,
        ref_schema=row.get("referred_schema"),
        ref_columns=tuple(str(one) for one in (row.get("referred_columns") or ())),
    )


#: Rough breadth of a type, only to choose between two unique candidates. The engines
#: own the real vocabulary.
_WIDTHS = (
    "bigint", "int8", "numeric", "decimal", "money", "uniqueidentifier", "uuid",
    "varchar", "nvarchar", "char", "nchar", "text", "int", "integer", "int4",
    "mediumint", "smallint", "int2", "tinyint", "serial",
)


def _type_width(name: str) -> int:
    base = str(name).split("(")[0].strip().casefold()
    if base in _WIDTHS:
        return len(_WIDTHS) - _WIDTHS.index(base)
    return 0


# ---------------------------------------------------------------------- judging


class _Proposal:
    """Catalog in, draft document out, a reason attached to every guess."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.doc = GraphMapping(schema_name=catalog.schema)
        self.labels = _NameAllocator()
        self.types = _NameAllocator()

    def document(self) -> GraphMapping:
        linked = {table.name for table in self.catalog.tables if table.is_link_table()}
        records = [table for table in self.catalog.tables if table.name not in linked]
        links = [table for table in self.catalog.tables if table.name in linked]
        self.add_records(records)
        self.add_links(links)
        self.add_direct_keys(records)
        self.apply_caps()
        return self.doc

    # -- records ------------------------------------------------------------

    def add_records(self, records: Sequence[SchemaTable]) -> None:
        for table in records:
            label = self.labels.name_of(table.name)
            key = table.key_candidate()
            if key is None:
                self.doc.nodes.append(
                    MappingNode(
                        label=label, table=table.name, include=False, notes=_no_key_reason(table)
                    )
                )
                continue
            self.doc.nodes.append(
                MappingNode(
                    label=label,
                    table=table.name,
                    key=key,
                    properties=table.columns_except([key]),
                    notes=_record_reason(table, key),
                )
            )

    # -- junction tables ------------------------------------------------------

    def add_links(self, links: Sequence[SchemaTable]) -> None:
        for table in links:
            first, second = table.foreign_keys
            if not (self.catalog.has_table(first.ref_table) and self.catalog.has_table(second.ref_table)):
                self.warn(
                    "link table {!r} points outside the graph ({} , {}), so no edge is "
                    "drawn for it".format(table.name, first.ref_table, second.ref_table)
                )
                continue
            key = table.key_candidate()
            self.doc.edges.append(
                MappingEdge(
                    type=self.types.allocate(sanitize_identifier(table.name, upper=True)),
                    table=table.name,
                    key=key,
                    properties=table.columns_except(
                        {first.columns[0], second.columns[0]} | ({key} if key else set())
                    ),
                    endpoints=[
                        MappingPair(
                            src=self._end(table, first),
                            dst=self._end(table, second),
                            row_estimate=table.row_estimate,
                        )
                    ],
                    notes=_link_reason(table),
                )
            )

    def _end(self, table: SchemaTable, fk: ForeignKey) -> MappingEndpoint:
        column = fk.columns[0]
        return MappingEndpoint(
            label=self.labels.name_of(fk.ref_table),
            column=column,
            column_type=table.columns.get(column, UNKNOWN_TYPE),
        )

    # -- direct foreign keys ---------------------------------------------------

    def add_direct_keys(self, records: Sequence[SchemaTable]) -> None:
        """
        A foreign key on a record table also becomes an edge: the child references the
        parent, so the child is ``src``. That direction is a fact about the schema and
        not a guess, which is the whole reason zero-config is worth having.

        A table that could not be a node cannot source an edge either — one end of it
        would be a label nothing declares.
        """
        for table in records:
            key = table.key_candidate()
            if key is None:
                continue
            for fk in table.foreign_keys:
                if not fk.single:
                    self.warn(
                        "foreign key {!r} on {!r} is composite {}; the contract matches "
                        "one column per end, so no edge is drawn for it".format(
                            fk.name or fk.columns[0], table.name, list(fk.columns)
                        )
                    )
                    continue
                if not self.catalog.has_table(fk.ref_table):
                    self.warn(
                        "foreign key {}.{} points at {!r}, which is not in the graph, so "
                        "no edge is drawn".format(table.name, fk.columns[0], fk.ref_table)
                    )
                    continue
                column = fk.columns[0]
                self.doc.edges.append(
                    MappingEdge(
                        type=self.types.allocate(fk_name(fk, table.name, fk.ref_table)),
                        table=table.name,
                        key=key,
                        endpoints=[
                            MappingPair(
                                src=MappingEndpoint(
                                    label=self.labels.name_of(table.name),
                                    column=key,
                                    column_type=table.columns.get(key, UNKNOWN_TYPE),
                                ),
                                dst=MappingEndpoint(
                                    label=self.labels.name_of(fk.ref_table),
                                    column=column,
                                    column_type=table.columns.get(column, UNKNOWN_TYPE),
                                ),
                                row_estimate=table.row_estimate,
                            )
                        ],
                        notes=_fk_reason(table, fk),
                    )
                )

    # -- caps ----------------------------------------------------------------

    def apply_caps(self) -> None:
        """Report the cap it hit by name: a short answer with no reason is a bug report."""
        self._cap("labels", MAX_LABELS, self.doc.nodes)
        self._cap("edges", MAX_EDGES, self.doc.edges)
        for name, record in [(node.label, node) for node in self.doc.nodes] + [
            (edge.type, edge) for edge in self.doc.edges
        ]:
            if len(record.properties) > MAX_PROPS:
                self.warn(
                    "truncated: the props cap is {}, so {} column(s) of {!r} are not "
                    "mapped — add them by name if they matter".format(
                        MAX_PROPS, len(record.properties) - MAX_PROPS, name
                    )
                )
                record.properties = dict(list(record.properties.items())[:MAX_PROPS])
        if self.catalog.skipped:
            self.warn(
                "{} table(s) were never considered (views and engine internals): {}".format(
                    len(self.catalog.skipped), ", ".join(sorted(set(self.catalog.skipped))[:20])
                )
            )

    def _cap(self, kind: str, limit: int, entries: List[Any]) -> None:
        """
        Keep the biggest records, and say both which cap dropped the rest and how it ranked.

        Ranking is the count the catalog read, with an uncounted table placed *ahead* of a
        table counted at zero: nobody proved the first one small. Where no statistics were
        gathered at all every record ties, and the only thing left to order by is the order
        the catalog returned - which is worth saying out loud, because it is what decides
        whether a person's 4-million-row table survives ``max_labels``.
        """
        kept = [entry for entry in entries if entry.include]
        if len(kept) <= limit:
            return
        ranked = sorted(kept, key=self._rank, reverse=True)
        for entry in ranked[limit:]:
            entry.include = False
            entry.notes = "not mapped: the {} cap is {}".format(kind, limit)
        uncounted = sum(1 for entry in kept if self._rows(entry) is None)
        tiebreak = (
            "{} of them carry no row count, so they keep their catalog order among "
            "themselves".format(uncounted)
            if uncounted
            else "ordered by rows counted, uncounted ahead of any counted empty"
        )
        self.warn(
            "truncated: the {} cap is {} and this schema offers {}; {} are left out — {}; "
            "choose them with include/exclude".format(
                kind, limit, len(kept), len(kept) - limit, tiebreak
            )
        )

    def _rows(self, entry: Any) -> Optional[int]:
        """What the catalog counted for the table this record was read from."""
        found = self.catalog.by_name.get(entry.table)
        return found.row_estimate if found is not None else None

    def _rank(self, entry: Any) -> float:
        """Unknown just above zero, below any real count: the most a gap can be worth."""
        rows = self._rows(entry)
        return 0.5 if rows is None else float(rows)

    def warn(self, message: str) -> None:
        self.catalog.warnings.append(message)


class _NameAllocator:
    """Legal names, ``_2`` on a collision, and one label per table however often it is named."""

    def __init__(self) -> None:
        self.used: Set[str] = set()
        self.by_table: Dict[str, str] = {}

    def allocate(self, name: str) -> str:
        wanted = sanitize_identifier(name)
        found = wanted
        suffix = 2
        while found.casefold() in self.used:
            found = "{}_{}".format(wanted, suffix)
            suffix += 1
        self.used.add(found.casefold())
        return found

    def name_of(self, table: str) -> str:
        if table not in self.by_table:
            self.by_table[table] = self.allocate(table)
        return self.by_table[table]


def sanitize_identifier(name: str, upper: bool = False) -> str:
    """
    A name every one of the three engines accepts unquoted and an id can carry.

    Two tables whose names differ only by case would collide here, and that is the
    point: they cannot both be a label, so the collision suffix is what makes the
    second one visible instead of silent.
    """
    text = _bare(name)
    out = "".join(char if char.isalnum() or char == "_" else "_" for char in text)
    if not out:
        return "UNNAMED"
    if out[0].isdigit():
        out = "_" + out
    return out.upper() if upper else out


def fk_name(fk: ForeignKey, child: str, parent: str) -> str:
    """Sanitized ``upper(constraint_name)``, else ``upper(child_column_parent)``."""
    if fk.name:
        return sanitize_identifier(fk.name, upper=True)
    return sanitize_identifier(
        "{}_{}_{}".format(child, fk.columns[0] if fk.columns else "", parent), upper=True
    )


def _no_key_reason(table: SchemaTable) -> str:
    if table.pk_columns:
        return "not a node: its primary key is {} and the contract matches one column per key".format(
            list(table.pk_columns)
        )
    return (
        "not a node: no single-column primary key and no single-column NOT NULL unique, "
        "so there is nothing to build <Label>:<key> from"
    )


def _record_reason(table: SchemaTable, key: str) -> str:
    if len(table.pk_columns) == 1:
        return "record table keyed by {} ({}), its primary key".format(
            key, table.columns.get(key, UNKNOWN_TYPE)
        )
    return "record table keyed by {} — a single-column unique, not the primary key".format(key)


def _link_reason(table: SchemaTable) -> str:
    first, second = table.foreign_keys
    if first.ref_table == second.ref_table:
        return (
            "both ends point at {} and nothing points back at it, so which way the arrow "
            "means is a decision, not a fact: check the direction".format(first.ref_table)
        )
    return (
        "link table: exactly two foreign keys ({} -> {}, {} -> {}) and nothing points "
        "back at it".format(first.columns[0], first.ref_table, second.columns[0], second.ref_table)
    )


def _fk_reason(table: SchemaTable, fk: ForeignKey) -> str:
    column = fk.columns[0] if fk.columns else "?"
    if fk.ref_table == table.name:
        return "{}.{} points back at {} itself, so the edge has one label on both ends".format(
            table.name, column, table.name
        )
    return (
        "foreign key {} -> {} is the direction the schema states, so {} is the source; "
        "the link carries no columns of its own".format(column, fk.ref_table, table.name)
    )
