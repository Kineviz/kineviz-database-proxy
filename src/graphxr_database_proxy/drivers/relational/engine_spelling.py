"""
``EngineSpelling``: the abstract surface every relational engine answers through.

Split out of ``engine.py``. This is what a new backend subclasses and the only
place an engine divergence may be expressed; nothing else in the seam branches
on an engine's name."""

from __future__ import annotations

import abc
from typing import Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import URL, text
from sqlalchemy.types import String, TypeEngine

from .engine_config import ConnectConfig
from .engine_server import ProbedConnection, ServerInfo
from .engine_terms import _CONTROL_CHARS, _IDENTIFIER_SAFE, NameCaseRule
from .engine_types import DeclaredType, parse_declared_type


class UnbindableType(Exception):
    """
    A declared type this engine has no binding for, so a value cannot be sent as that column.

    Raised by whoever needed the type, because only that caller knows what was being
    asked: the bind seam names the column and the value's journey, ``compile`` names the
    label or link whose key it is. The engine's own answer is the bare ``None`` — the
    refusal to guess — and never a stand-in it would be impossible to tell apart from a
    real one downstream.
    """


class EngineSpelling(abc.ABC):
    """One engine's answers. A new relational backend subclasses this; it does not
    get a second copy of the SQL generator."""

    #: == the ``DatabaseType`` value and the registry key.
    name: str = ""
    #: The SQLAlchemy ``+<dbapi>`` token, which is also the module the DBAPI installs.
    dbapi: str = ""
    #: What to tell a person to install when that module is missing.
    pip_extra: str = ""
    default_port: int = 0
    #: ``dbo`` / ``public`` / ``None`` — on MySQL the schema *is* the database.
    default_schema: Optional[str] = None
    supports_regex: bool = False
    #: ``fold-lower`` / ``insensitive`` / ``sensitive``; see :data:`NameCaseRule`.
    name_case_rule: NameCaseRule = "sensitive"
    #: A name longer than this is silently truncated (PostgreSQL) or refused.
    max_identifier_length: int = 128
    #: How many table names one statistics round trip may carry.
    names_page_size: int = 500

    # -- connection --------------------------------------------------------

    @abc.abstractmethod
    def url(self, config: ConnectConfig, *, password: Optional[str] = None) -> URL:
        """The connect URL, with the password in the URL object and nowhere else.

        ``URL`` masks it in ``repr`` and ``str``; :meth:`connect_args` never carries
        a secret, so a logged dict of connect arguments is safe to read.
        """

    @abc.abstractmethod
    def connect_args(self, config: ConnectConfig) -> Dict[str, object]:
        """Timeouts, TLS and charset for the DBAPI. No secrets, ever."""

    @abc.abstractmethod
    def session_statements(self) -> Tuple[str, ...]:
        """Statements to run once per new connection, before any query.

        Per-connection setup only: the isolation level goes through
        ``execution_options`` because SQLAlchemy owns the reset-on-return handling
        for it, and re-issuing it by hand would leave a pooled connection with a
        level nobody asked for.
        """

    @abc.abstractmethod
    def isolation(self, config: ConnectConfig) -> Optional[str]:
        """The ``isolation_level`` execution option, or None to leave the server's."""

    def timeout_hint(self, config: ConnectConfig) -> str:
        """A hint to place straight after a top-level ``SELECT``, or "".

        Only MySQL has one. The engines without it guard a runaway query at
        connection level, which is the same promise with a coarser boundary.
        """
        return ""

    def read_only_statement(self) -> Optional[str]:
        """The statement that starts a read-only transaction, or None.

        Per transaction, because that is the only boundary two of these engines
        offer; a session-level guard, where one exists, is
        :meth:`session_statements`. A caller that starts a transaction of its own has
        to send this first, or its reads are not read-only.
        """
        return None

    def data_api_name(self) -> str:
        """The extra to name in a helpful ``ImportError``."""
        return self.pip_extra or self.dbapi

    def missing_dbapi_message(self) -> str:
        """What to say when the DBAPI is not installed."""
        return (
            f"{self.name} projects need the {self.data_api_name()} package, which this "
            f"proxy installs as an optional extra: pip install 'graphxr-database-proxy"
            f"[{self.name}]' (or pip install {self.data_api_name()})."
        )

    # -- types -------------------------------------------------------------

    @abc.abstractmethod
    def type_table(self) -> Mapping[str, str]:
        """Declared base type (lower-case, args stripped) -> the shared vocabulary."""

    def normalise_type(
        self, db_type: str, *, precision: Optional[int] = None, scale: Optional[int] = None
    ) -> str:
        """A column's declared type as one of :data:`PROPERTY_TYPES`.

        Unknown becomes ``STRING`` and is still reported by :meth:`is_known_type`:
        dropping a property because its type was unfamiliar is how a person loses a
        column they can plainly see in the source database.
        """
        declared = parse_declared_type(db_type, precision=precision, scale=scale)
        if declared.array:
            # No list type in the vocabulary, and a property that arrives as a Python
            # list is not something FastAPI can put on the wire either.
            return "STRING"
        refined = self.refine_type(declared)
        if refined is not None:
            return refined
        return self.type_table().get(declared.base, "STRING")

    def refine_type(self, declared: DeclaredType) -> Optional[str]:
        """Where the arguments decide. Return None to fall through to the table."""
        return None

    def is_known_type(self, db_type: str) -> bool:
        """Whether :meth:`normalise_type` matched rather than defaulted to ``STRING``."""
        declared = parse_declared_type(db_type)
        if declared.array:
            return False
        if self.refine_type(declared) is not None:
            return True
        return declared.base in self.type_table()

    @abc.abstractmethod
    def column_type(self, declared_type: Optional[str]) -> Optional[TypeEngine]:
        """This engine's own type for a declared column type, or None when it has none.

        This is the seam's answer to the bridge's ``param_type_for``: pyodbc sent
        every Python string as ``nvarchar``, and comparing one against a ``varchar``
        or ``bigint`` column made SQL Server convert *the column*, turning an index
        lookup into a full scan with nothing in the output to say so. SQLAlchemy
        binds typed parameters, so the remaining job is to hand it the real type —
        which is why ``normalise_type`` loses the distinction and ``column_type``
        must not.

        The one answer it must not give either is a text type for a name it does not
        know: that is indistinguishable from the real thing downstream, and it is how a
        key on a ``geometry`` column became a text parameter that matched nothing. Say
        None, and let the caller say what that costs (:exc:`UnbindableType`).
        """

    def declared_column_type(self, declared_type: Optional[str]) -> TypeEngine:
        """A type to declare a column with: the engine's own, or text when it has none.

        Only a bind can be answered wrong by an unnamed type, so only a bind may refuse.
        A projected column is never sent anywhere; the worst its type can cost is how the
        value is decoded. This is where that choice is allowed to be made, out loud.
        """
        named = self.column_type(declared_type)
        return named if named is not None else String()

    def single_column_key_ok(
        self, declared_type: Optional[str], *, nullable: bool = False
    ) -> bool:
        """Can a table's identity be ``<Label>:<key>`` through this column?

        A column the engine cannot index cannot hold a unique constraint, so a
        mapping that used it as a key would mint colliding ids for different rows.
        """
        if nullable:
            return False
        declared = parse_declared_type(declared_type or "")
        if declared.array:
            return False
        return declared.base not in self.unkeyable_types()

    def unkeyable_types(self) -> frozenset:
        """Declared base types that cannot carry a single-column key on this engine."""
        return frozenset()

    # -- casts -------------------------------------------------------------

    @abc.abstractmethod
    def agg_cast(self, function: str, column_type: Optional[str]) -> Optional[str]:
        """The SQL type to cast a column to before ``function``, or None.

        ``None`` is the common answer and the load-bearing one. On PostgreSQL
        ``avg`` is already ``numeric`` and ``sum(int4)`` is already ``bigint``, so a
        cast buys nothing and a cast of a ``text`` expression breaks ``count(*)``
        shapes that read the same column.
        """

    def group_key_cast(self, column_type: Optional[str]) -> Optional[str]:
        """The type a grouping key must be narrowed to, or None.

        ``GROUP BY`` is illegal on the unbounded LOB types, and that refusal reaches
        the person as "my query does not work".
        """
        return None

    def like_cast(self, column_type: Optional[str]) -> Optional[str]:
        """The type a non-character column must become for a ``LIKE``, or None.

        ``None`` for character data, always: wrapping a column in ``CAST`` in order
        to compare it is exactly the move that stops an index being used.
        """
        return None

    # -- server ------------------------------------------------------------

    @abc.abstractmethod
    def probe_sql(self) -> str:
        """One SELECT that answers version and flags together."""

    @abc.abstractmethod
    def server_from_row(self, row: Sequence[object]) -> ServerInfo:
        """Turn :meth:`probe_sql`'s single row into a :class:`ServerInfo`."""

    def probe_server(self, connection: ProbedConnection) -> ServerInfo:
        """Ask the server what it is: one round trip, nothing cached here."""
        rows = list(connection.execute(text(self.probe_sql()), {}))
        if not rows:
            raise ValueError(f"{self.name} returned no row from its own version probe")
        return self.server_from_row(rows[0])

    @abc.abstractmethod
    def counts_statement(
        self, schema: Optional[str], names: Sequence[str]
    ) -> Tuple[str, Dict[str, object]]:
        """The statistics SELECT for one page of table names, plus its binds."""

    def approx_row_counts(
        self, connection: ProbedConnection, schema: Optional[str], tables: Sequence[str]
    ) -> Dict[str, int]:
        """Estimated rows per table, from engine statistics only — never a scan.

        Paged by name: the catalog lane hands over the page it is showing and this
        loops it if a page is larger than one round trip can carry. A table the
        statistics have nothing for is **absent**, not zero — an un-analysed InnoDB
        table reporting 0 rows and a table nobody has counted are different
        statements, and only one of them can be defended.
        """
        out: Dict[str, int] = {}
        names = [str(name) for name in tables]
        if not names:
            return out
        size = max(1, int(self.names_page_size))
        for start in range(0, len(names), size):
            chunk = names[start : start + size]
            sql, params = self.counts_statement(schema, chunk)
            for row in connection.execute(text(sql), params):
                key, value = row[0], row[1]
                if key is None or value is None:
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if count >= 0:
                    out[str(key)] = count
        return out

    # -- id lists ----------------------------------------------------------

    @abc.abstractmethod
    def id_list_strategy(self, info: ServerInfo, size: int) -> str:
        """``"inline"`` placeholders, one ``"bulk"`` parameter, or a ``"fanout"``."""

    @abc.abstractmethod
    def bulk_id_form(self, info: ServerInfo) -> str:
        """How a bulk list is spelled, for the compiler."""

    @abc.abstractmethod
    def json_document_type(self) -> str:
        """The declared type name a shredded id list is bound as.

        Only the engines whose bulk form is a table function over one JSON document
        are read here, and the answer has to be a name :meth:`column_type` knows: the
        document crosses the wire once, as one parameter, and its type is what decides
        whether the server is handed a document or the DBAPI's guess at one.
        """

    @abc.abstractmethod
    def shredded_column_type(self, declared_type: Optional[str]) -> str:
        """The type this engine declares the value column of a shred into.

        ``OPENJSON``'s ``WITH`` and ``JSON_TABLE``'s ``COLUMNS`` both demand a type for
        the column they produce: an undeclared shred answers its own default and the
        comparison then converts the column, not the value — the index loss the clause
        exists to prevent. The answer is pasted into that clause verbatim, so it has to
        be a declared name this engine's own vocabulary and :meth:`column_type` read.
        """

    @abc.abstractmethod
    def fanout_chunk_size(self, info: ServerInfo) -> int:
        """Ids per branch when the list has to be chopped up."""

    # -- names -------------------------------------------------------------

    def identifier_ok(self, name: str) -> bool:
        """Is this name acceptable in a mapping document?

        Names reach the server inside quoted identifiers, so quoting is not the
        risk. The risks are a name the engine silently rewrites or reads as
        something else, and a look-alike character typed into a browser form — hence
        ASCII word characters only, no leading digit, and the per-engine length cap.
        """
        candidate = str(name or "")
        if not candidate or _CONTROL_CHARS.search(candidate):
            return False
        if len(candidate) > self.max_identifier_length:
            return False
        return bool(_IDENTIFIER_SAFE.match(candidate))

    def identifier_error(self, name: str) -> str:
        """Why :meth:`identifier_ok` said no; empty when it said yes."""
        if self.identifier_ok(name):
            return ""
        candidate = str(name or "")
        if not candidate:
            return "the name is empty"
        if _CONTROL_CHARS.search(candidate):
            return "the name contains a control character"
        if len(candidate) > self.max_identifier_length:
            return (
                f"{self.name} allows at most {self.max_identifier_length} characters in a "
                f"name and this one has {len(candidate)}"
            )
        if candidate[0].isdigit():
            return "the name starts with a digit, which is legal only inside quotes"
        return "the name contains a character outside A-Z, a-z, 0-9 and _"
