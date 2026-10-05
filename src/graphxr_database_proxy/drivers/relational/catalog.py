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
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
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

#: Table names per ``get_multi_*`` call. Big enough that an ordinary schema is one or
#: two round trips, small enough that a monster schema keeps paging.
PAGE_SIZE = 100

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

#: One reflected record as the inspector hands it back. Keys are stable across
#: dialects; value types are not — a column's ``type`` is a ``TypeEngine``, not a str.
Row = Dict[str, Any]

#: Row estimates for one page of tables, implemented per engine elsewhere.
RowCounts = Callable[[Sequence[str]], Mapping[str, int]]


class Inspector(Protocol):
    """
    The four methods discovery needs, spelled the way SQLAlchemy's ``Inspector`` spells them.

    Anything with these works, which is the point: the tests use a fake object and no
    driver has to be installed to run them.
    """

    def get_table_names(self, schema: Optional[str] = None) -> List[str]: ...

    def get_multi_columns(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, List[Row]]: ...

    def get_multi_pk_constraint(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Row]: ...

    def get_multi_foreign_keys(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, List[Row]]: ...


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
    row_estimate: int = 0

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

#: Bracket and quote characters a name can arrive wrapped in, from either side.
IDENT_TRIM = "[]'\"`"


def _bare(name: object) -> str:
    """A name with whatever quoting the engine put around it taken back off."""
    return str(name).strip().strip(IDENT_TRIM)


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


def _read_pages(
    inspector: Inspector,
    names: Sequence[str],
    schema: Optional[str],
    page_size: int,
    row_counts: Optional[RowCounts],
) -> Dict[str, Dict[str, Any]]:
    """
    Columns, primary keys, foreign keys, unique candidates and estimates, per page.

    The page of *names* is the unit the inspector is asked in, so the cost is per page
    and nothing is held for the whole library at once.
    """
    size = max(1, int(page_size))
    out: Dict[str, Dict[str, Any]] = {"columns": {}, "pk": {}, "fk": {}, "unique": {}, "rows": {}}
    for start in range(0, len(names), size):
        page = list(names[start : start + size])
        out["columns"].update(_multi(inspector.get_multi_columns, page, schema))
        out["pk"].update(_multi(inspector.get_multi_pk_constraint, page, schema))
        out["fk"].update(_multi(inspector.get_multi_foreign_keys, page, schema))
        uniques: Dict[str, Tuple[str, ...]] = {}
        for table, rows in _optional_multi(inspector, "get_multi_unique_constraints", page, schema).items():
            uniques[table] = _single_columns(rows, "constrained_columns")
        for table, rows in _optional_multi(inspector, "get_multi_indexes", page, schema).items():
            uniques[table] = tuple(list(uniques.get(table, ())) + _unique_index_columns(rows))
        out["unique"].update(uniques)
        if row_counts is not None:
            out["rows"].update({str(k): int(v or 0) for k, v in dict(row_counts(page)).items()})
    return out


def _multi(
    call: Callable[..., Dict[str, Any]], page: Sequence[str], schema: Optional[str]
) -> Dict[str, Any]:
    """One ``get_multi_*`` call, keyed by bare table name however the dialect keys it."""
    return _bare_keys(call(tables=list(page), schema=schema) or {}, page)


def _optional_multi(
    inspector: Inspector, method: str, page: Sequence[str], schema: Optional[str]
) -> Dict[str, Any]:
    return _bare_keys(_call(inspector, method, page=page, schema=schema) or {}, page)


def _probe_names(inspector: Inspector, method: str, schema: Optional[str]) -> List[str]:
    return [str(name) for name in _call(inspector, method, page=None, schema=schema) or []]


def _call(
    inspector: Inspector, method: str, page: Optional[Sequence[str]], schema: Optional[str]
) -> Any:
    """
    An optional reflection method, or one this dialect cannot answer.

    Absent and unsupported are both gaps rather than errors: a view list a MySQL 5.x
    build withholds must not fail the discovery of the tables it does have.
    """
    probe = getattr(inspector, method, None)
    if probe is None:
        return None
    try:
        return probe(tables=list(page) if page is not None else None, schema=schema)
    except TypeError:
        try:
            return probe(schema=schema) if page is None else None
        except Exception:
            return None
    except Exception:
        return None


def _bare_keys(payload: Mapping[str, Any], page: Sequence[str]) -> Dict[str, Any]:
    """
    Index one reflection payload by table name, however the dialect keyed it.

    SQLAlchemy keys by table name; some paths answer ``{"dbo.Person": …}`` and a
    schema-level reflection answers ``{"dbo": {"Person": …}}``. Only the names actually
    asked about on this page are read, so an unexpected shape is skipped rather than
    mistaken for a table.
    """
    wanted = {str(name) for name in page}
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        text = str(key)
        name = text.rsplit(".", 1)[-1]
        if name in wanted:
            out[name] = value
            continue
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                if str(inner) in wanted:
                    out[str(inner)] = inner_value
    return out


def _single_columns(rows: Sequence[Any], key: str) -> Tuple[str, ...]:
    out: List[str] = []
    for row in rows or ():
        cols = list((row or {}).get(key) or [])
        if len(cols) == 1:
            out.append(str(cols[0]))
    return tuple(out)


def _unique_index_columns(rows: Sequence[Any]) -> Tuple[str, ...]:
    out: List[str] = []
    for row in rows or ():
        entry = row or {}
        cols = list(entry.get("column_names") or [])
        if len(cols) == 1 and entry.get("unique"):
            out.append(str(cols[0]))
    return tuple(out)


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
                row_estimate=int(pages["rows"].get(name) or 0),
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


def type_name(value: object) -> str:
    """
    The SQL name of whatever the inspector called a column's type.

    A reflected type is a ``TypeEngine`` instance, and importing sqlalchemy to say so
    is not a price worth paying for a string: ``compile()`` is how it spells itself.
    """
    if value is None:
        return UNKNOWN_TYPE
    if isinstance(value, str):
        return value.strip() or UNKNOWN_TYPE
    spell = getattr(value, "compile", None)
    if callable(spell):
        try:
            return str(spell()).strip() or UNKNOWN_TYPE
        except Exception:  # a type that cannot spell itself is still a column
            pass
    return str(value).strip() or UNKNOWN_TYPE


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
        kept = [entry for entry in entries if entry.include]
        if len(kept) <= limit:
            return
        for entry in kept[limit:]:
            entry.include = False
            entry.notes = "not mapped: the {} cap is {}".format(kind, limit)
        self.warn(
            "truncated: the {} cap is {} and this schema offers {}; {} are left out — "
            "choose them with include/exclude".format(kind, limit, len(kept), len(kept) - limit)
        )

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
