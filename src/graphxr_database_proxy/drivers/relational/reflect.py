"""
The paged read of a live schema, kept apart from the rules that judge what it read.

Discovery asks an inspector for its table names once, then columns, primary keys,
foreign keys, unique candidates and row estimates *one page of names at a time*. That
paging is the reason a 3 000-table ERP can be mapped at all, and it is the invariant
easiest to lose in review: a judgement fix that quietly turns a page into a whole-library
read looks harmless in a file whose other half is classification rules. So the walk lives
here, where its tests can only be about the walk.

Nothing in this file decides what a table *means*. Reflection payloads come back keyed by
bare table name; ``catalog.py`` turns them into ``SchemaTable`` objects and judges them.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from ...models.graph_mapping import UNKNOWN_TYPE
#: Table names per ``get_multi_*`` call. Big enough that an ordinary schema is one or
#: two round trips, small enough that a monster schema keeps paging.
PAGE_SIZE = 100

#: One reflected record as the inspector hands it back. Keys are stable across
#: dialects; value types are not — a column's ``type`` is a ``TypeEngine``, not a str.
Row = Dict[str, Any]

#: Row estimates for one page of tables, implemented per engine elsewhere.
RowCounts = Callable[[Sequence[str]], Mapping[str, int]]

#: What each optional probe contributes, keyed by the method, so a probe that *threw*
#: can be reported as the half of the read that went missing rather than as a stack trace.
PROBE_LOSS: Dict[str, str] = {
    "get_multi_unique_constraints": "single-column unique candidates",
    "get_multi_indexes": "unique index columns",
    "get_view_names": "the list of views, which is what keeps a view out of the tables",
}

#: ``(method, failure) -> how many times it threw``. A page of names throws once per page,
#: so the tally is what keeps a 3 000-table schema from answering 30 identical warnings.
Gaps = Dict[Tuple[str, str], int]


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


#: Bracket and quote characters a name can arrive wrapped in, from either side.
IDENT_TRIM = "[]'\"`"


def _bare(name: object) -> str:
    """A name with whatever quoting the engine put around it taken back off."""
    return str(name).strip().strip(IDENT_TRIM)


def _read_pages(
    inspector: Inspector,
    names: Sequence[str],
    schema: Optional[str],
    page_size: int,
    row_counts: Optional[RowCounts],
    gaps: Optional[Gaps] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Columns, primary keys, foreign keys, unique candidates and estimates, per page.

    The page of *names* is the unit the inspector is asked in, so the cost is per page
    and nothing is held for the whole library at once. A probe that throws adds to
    ``gaps`` rather than answering nothing in silence — see :func:`_call`.
    """
    size = max(1, int(page_size))
    out: Dict[str, Dict[str, Any]] = {"columns": {}, "pk": {}, "fk": {}, "unique": {}, "rows": {}}
    for start in range(0, len(names), size):
        page = list(names[start : start + size])
        out["columns"].update(_multi(inspector.get_multi_columns, page, schema))
        out["pk"].update(_multi(inspector.get_multi_pk_constraint, page, schema))
        out["fk"].update(_multi(inspector.get_multi_foreign_keys, page, schema))
        uniques: Dict[str, Tuple[str, ...]] = {}
        for table, rows in _optional_multi(
            inspector, "get_multi_unique_constraints", page, schema, gaps
        ).items():
            uniques[table] = _single_columns(rows, "constrained_columns")
        for table, rows in _optional_multi(inspector, "get_multi_indexes", page, schema, gaps).items():
            uniques[table] = tuple(list(uniques.get(table, ())) + _unique_index_columns(rows))
        out["unique"].update(uniques)
        if row_counts is not None:
            # Answering nothing for a page is a normal answer, not a failure: this page of
            # names has already been paid for, so the walk finishes and the tables it read
            # stay uncounted rather than becoming an empty schema.
            counted = row_counts(page)
            if hasattr(counted, "items"):
                for key, value in counted.items():
                    try:
                        out["rows"][_bare(key)] = max(0, int(value))
                    except (TypeError, ValueError):
                        continue
    return out


def _multi(
    call: Callable[..., Dict[str, Any]], page: Sequence[str], schema: Optional[str]
) -> Dict[str, Any]:
    """One ``get_multi_*`` call, keyed by bare table name however the dialect keys it."""
    return _bare_keys(call(tables=list(page), schema=schema) or {}, page)


def _optional_multi(
    inspector: Inspector,
    method: str,
    page: Sequence[str],
    schema: Optional[str],
    gaps: Optional[Gaps] = None,
) -> Dict[str, Any]:
    return _bare_keys(_call(inspector, method, page=page, schema=schema, gaps=gaps) or {}, page)


def _probe_names(
    inspector: Inspector, method: str, schema: Optional[str], gaps: Optional[Gaps] = None
) -> List[str]:
    return [str(name) for name in _call(inspector, method, page=None, schema=schema, gaps=gaps) or []]


def _accepts_tables(probe: Callable[..., Any]) -> bool:
    """
    Whether this probe can be handed ``tables=``, which decides what a TypeError means.

    A probe that never took the keyword is answering a *signature* question — the reason
    the retry below exists — while a TypeError raised from inside a probe that does take it
    is a reflection failure. They arrive as the same exception, and today they are treated
    the same; the measurement that opened this file found the real one
    (``NullType() takes no arguments``, MySQL's own parser on a ``vector(3)`` column) in the
    second family, where it reads as a dialect that has no such method and the page simply
    loses whatever that probe answers.
    """
    try:
        parameters = inspect.signature(probe).parameters
    except (TypeError, ValueError):
        return True  # nothing to read it off: assume the call was well-formed and it threw
    if any(one.kind is one.VAR_KEYWORD for one in parameters.values()):
        return True
    return "tables" in parameters


def _gap(gaps: Optional[Gaps], method: str, exc: BaseException) -> None:
    """Record one thrown probe, counted, so a whole schema failing the same way says it once."""
    if gaps is None:
        return
    failure = "%s: %s" % (type(exc).__name__, str(exc).strip() or "the probe gave no message")
    key = (method, failure)
    gaps[key] = gaps.get(key, 0) + 1


def gap_warnings(gaps: Mapping[Tuple[str, str], int]) -> List[str]:
    """One sentence per thrown probe: what threw, how often, and which half of the read is gone."""
    out: List[str] = []
    for (method, failure), count in sorted(gaps.items()):
        loss = PROBE_LOSS.get(method, "whatever %s answers" % method)
        again = "" if count == 1 else " (%d times)" % count
        out.append(
            "%s%s raised %s, so the names read with it have no %s" % (method, again, failure, loss)
        )
    return out


def _call(
    inspector: Inspector,
    method: str,
    page: Optional[Sequence[str]],
    schema: Optional[str],
    gaps: Optional[Gaps] = None,
) -> Any:
    """
    An optional reflection method, or one this dialect cannot answer.

    Absent and unsupported are both gaps rather than errors: a view list a MySQL 5.x
    build withholds must not fail the discovery of the tables it does have. A probe that
    *throws* is a third and different fact — the read goes on without its answer either
    way, and the only thing that changes here is that ``gaps`` now says so.
    """
    probe = getattr(inspector, method, None)
    if probe is None:
        return None
    try:
        return probe(tables=list(page) if page is not None else None, schema=schema)
    except NotImplementedError:
        return None  # the dialect's own "I have no such list", which stays silent
    except TypeError as exc:
        if _accepts_tables(probe):
            _gap(gaps, method, exc)
            return None
        try:
            return probe(schema=schema) if page is None else None
        except NotImplementedError:
            return None
        except Exception as inner:
            if page is not None:
                return None  # no unpaged form of a per-page probe exists to fall back on
            _gap(gaps, method, inner)
            return None
    except Exception as exc:
        _gap(gaps, method, exc)
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


def dialect_of(inspector: Inspector) -> Any:
    """
    The dialect an inspector carries, when it carries one.

    A real ``sqlalchemy.Inspector`` does, and it is the only party that knows how this
    engine spells a type; a duck-typed inspector from a test may not, and gets the
    argument-free spelling the walk has always produced. Read, never imported — the
    protocol in this file is duck-typed on purpose.
    """
    return getattr(inspector, "dialect", None)


def type_name(value: object, dialect: object = None) -> str:
    """
    The SQL name of whatever the inspector called a column's type.

    A reflected type is a ``TypeEngine`` instance, and importing sqlalchemy to say so
    is not a price worth paying for a string: ``compile()`` is how it spells itself.

    ``dialect`` is that instance's own, when the reader has one, and it is not decoration:
    several types spell differently per backend, and the name written down here is the
    only type information a bind is later chosen from. ``ARRAY(TEXT)`` spells ``ARRAY``
    to the generic compiler — SQLAlchemy's class name, which no engine declares — and
    ``TEXT[]`` to PostgreSQL's, which is the column. Without a dialect nothing is asked
    for one, so a duck-typed inspector keeps answering what it always answered.
    """
    if value is None:
        return UNKNOWN_TYPE
    if isinstance(value, str):
        return value.strip() or UNKNOWN_TYPE
    spell = getattr(value, "compile", None)
    if callable(spell):
        for arguments in ((dialect,), ()) if dialect is not None else ((),):
            try:
                return str(spell(*arguments)).strip() or UNKNOWN_TYPE
            except Exception:  # a type that cannot spell itself is still a column
                continue
    return str(value).strip() or UNKNOWN_TYPE


