"""The relational seam: what each engine has to answer, and why each answer exists.

The bridge this replaces spelled T-SQL by hand (``ibase_bridge/tsql.py``,
``connection.py``). Most of what it guarded against is now SQLAlchemy's job:
identifier quoting, ``TOP``/``ROW_NUMBER``/``LIMIT`` paging, ``LIKE`` escaping and
typed bound parameters come out of the compiler, so ``quote``, ``top_clause``,
``offset_fetch``, ``like_pattern`` and ``LIKE_ESCAPE`` have no twin here. What the
compiler does *not* know is server policy, and that is the whole of this module:

* **which statement to send on connect** — ``ARITHABORT`` off splits SQL Server's
  plan cache against SSMS, which is the classic "fast in SSMS, slow from the app";
* **what the server can do** — ``OPENJSON`` needs a 2016 engine *and* a database
  left at compatibility level 130, because a database restored from an older server
  keeps its old level;
* **which cast is load-bearing** — ``AVG`` over an integer column is integer
  division on SQL Server (``avg(1,2,2)`` answers ``1``, silently) and ``SUM`` over
  an integer column overflows instead of widening. PostgreSQL needs neither, and a
  cast there would break ``count``;
* **how a big id list crosses the wire** — 2 100 parameters is a hard ceiling on
  SQL Server, and a selection of tens of thousands of ids is a normal GraphXR expand;
* **where row counts come from without a scan** — engine statistics only, paged by
  name.

Nothing here opens a connection: no DBAPI is imported and ``create_engine`` is never
called, so the module loads on a venv that has none of the optional extras.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple

from sqlalchemy import URL, text
from sqlalchemy.types import TypeEngine

# ---------------------------------------------------------------------------
# vocabularies — closed sets, so a caller can branch on them without guessing
# ---------------------------------------------------------------------------

#: The shared property-type vocabulary. The numeric members are deliberately the
#: spellings ``drivers/dialect.py`` keeps in ``_NUMERIC_KEY_TYPES``, which is
#: compared lowercased: ``render_key_literal`` quotes a key unless
#: ``is_numeric_key_type`` says the column is numeric, so a name outside that set
#: silently turns ``WHERE n.person_id IN [1001]`` into ``IN ["1001"]``.
#: ``test_vocabulary_numeric_names_still_read_as_numeric_keys`` pins it.
PropertyType = str

PROPERTY_TYPES: Tuple[str, ...] = (
    "INT64",
    "INT32",
    "DOUBLE",
    "DECIMAL",
    "STRING",
    "BOOL",
    "DATE",
    "TIMESTAMP",
    "BYTES",
    "JSON",
)

#: The members of that vocabulary a key column may be typed as numerically.
NUMERIC_PROPERTY_TYPES: Tuple[str, ...] = ("INT64", "INT32", "DOUBLE", "DECIMAL")

#: How a list of key literals reaches the server.
IdListStrategy = str
ID_LIST_STRATEGIES: Tuple[str, ...] = ("inline", "bulk", "fanout")

#: The shape of a ``bulk`` list, once the strategy says this server can carry one.
BulkIdForm = str
BULK_ID_FORMS: Tuple[str, ...] = ("openjson", "any", "json_table", "placeholders")

#: How ``Order`` and ``ORDER`` relate, once the name is quoted on the way in.
#: ``fold-lower`` means an *unquoted* name silently becomes another table, which is
#: why every identifier here is quoted; ``insensitive`` means the collation decides
#: and the two are one table; ``sensitive`` means they are two tables.
NameCaseRule = str

# ---------------------------------------------------------------------------
# numbers that are engine truth rather than tuning
# ---------------------------------------------------------------------------

#: SQL Server refuses more than this many parameters in one statement.
MSSQL_MAX_PARAMS = 2100
#: Above this many ids, an ``IN`` list is both close to that ceiling and unreadable
#: in a log.
MSSQL_INLINE_ID_MAX = 2000
#: SQL Server 2016: the first release with ``OPENJSON``.
MSSQL_OPENJSON_MIN_MAJOR = 13
#: ...and the *database* must be at this compatibility level for it to resolve.
MSSQL_OPENJSON_MIN_COMPAT = 130
#: SQL Server 2012: the first release with ``OFFSET ... FETCH NEXT``, which is how
#: every result set here is paged. Older servers cannot be served at all.
MSSQL_MIN_SUPPORTED_MAJOR = 11
#: A fan-out sends this many ids per branch, leaving room for the rest of the
#: statement under the parameter ceiling.
MSSQL_FANOUT_CHUNK = 1000

#: MySQL 8.0.4: the release with ``JSON_TABLE``.
MYSQL_JSON_TABLE_MIN = (8, 0, 4)
#: The same inline crossover as SQL Server, so one number explains both "does it
#: fit" and "is the statement still readable".
MYSQL_INLINE_ID_MAX = 2000
MYSQL_FANOUT_CHUNK = 1000
#: 8.0 is where CTEs and window functions landed; a plan builder that uses either
#: cannot serve 5.7, and MariaDB reports ``10.x``/``11.x`` here and is not measured.
MYSQL_MIN_SUPPORTED = (8, 0)

#: PostgreSQL has no uncommitted reads but accepts the words: it treats
#: ``READ UNCOMMITTED`` as an alias for ``READ COMMITTED``. It is therefore never
#: offered, never passed through, and a request for it is refused by name.
POSTGRESQL_REFUSED_ISOLATION = "READ UNCOMMITTED"
#: Small id lists stay an ``IN`` on PostgreSQL: there is no parameter ceiling to
#: dodge, so the crossover is about a plan a reader recognises in a log.
POSTGRESQL_INLINE_ID_MAX = 100
#: PostgreSQL refuses to page at all below this; 9.6 is where the catalog reads and
#: statistics views this seam needs all exist.
POSTGRESQL_MIN_SUPPORTED = (9, 6)

#: The bridge's query timeout, kept as the default: a runaway analytical query
#: should end on its own rather than hold a pooled connection until something
#: upstream gives up.
DEFAULT_QUERY_TIMEOUT_SECONDS = 120
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

#: ``config.options`` keys this seam reads, on every engine.
ISOLATION_OPTION = "isolation_level"
CONNECT_TIMEOUT_OPTION = "connect_timeout"
QUERY_TIMEOUT_OPTION = "query_timeout"
CONNECT_ARGS_OPTION = "connect_args"
URL_QUERY_OPTION = "url_query"
SCHEMA_OPTION = "schema"

_IDENTIFIER_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class UnknownEngine(ValueError):
    """Raised for a database type no engine spelling is registered for.

    Refusing is the point. The client's proxy tables fall through to the bolt family
    when a backend has no case there, and a wrong-but-plausible dialect is what gave
    a Kuzu project ``ID(n) IN ["<Label>:<key>"]`` against an ``INTERNAL_ID`` column.
    A second copy of that mistake belongs nowhere near this registry.
    """


@dataclass(frozen=True)
class ServerInfo:
    """What one probe round trip learned about a server.

    Fields are facts; the ``supports_*`` properties are the same facts read as
    policy, so no caller re-derives a version comparison at the call site.
    """

    engine: str
    version: str = ""
    major: int = 0
    minor: int = 0
    #: SQL Server's ``ProductBuild``, PostgreSQL's and MySQL's patch number.
    build: int = 0
    #: SQL Server only: ``sys.databases.compatibility_level``. 0 means unknown.
    compat_level: int = 0
    #: ``SERVERPROPERTY('Edition')`` / MySQL's ``version_comment`` / ``version()``.
    edition: str = ""
    #: Plain words for the release: ``"2017"``, ``"16.4"``, ``"MariaDB 11.4"``.
    release_name: str = ""
    #: This server or database is not writable: a readable standby, an Azure
    #: readable secondary, or a server with ``read_only`` on.
    read_only_server: bool = False
    #: The ``mysql`` driver is facing MariaDB, which spells versions ``10.x``/``11.x``.
    mariadb: bool = False
    #: Whether ``Order`` and ``ORDER`` are one table. MySQL answers this from
    #: ``lower_case_table_names``; ``None`` means the engine's own rule applies.
    names_case_insensitive: Optional[bool] = None

    @property
    def release(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.build)

    def at_least(self, major: int, minor: int = 0, build: int = 0) -> bool:
        return self.release >= (major, minor, build)

    @property
    def supports_openjson(self) -> bool:
        """Engine and database both past the bar, or a big list goes out as chunks."""
        return (
            self.engine == "mssql"
            and self.major >= MSSQL_OPENJSON_MIN_MAJOR
            and self.compat_level >= MSSQL_OPENJSON_MIN_COMPAT
        )

    @property
    def supports_json_table(self) -> bool:
        if self.engine != "mysql" or self.mariadb:
            return False
        return self.release >= MYSQL_JSON_TABLE_MIN

    @property
    def supports_offset_fetch(self) -> bool:
        if self.engine == "mssql":
            return self.major >= MSSQL_MIN_SUPPORTED_MAJOR
        return True

    @property
    def supported(self) -> bool:
        """Can this server be served at all, as opposed to served badly."""
        if self.engine == "mssql":
            return self.major >= MSSQL_MIN_SUPPORTED_MAJOR
        if self.engine == "mysql":
            return (not self.mariadb) and self.release >= MYSQL_MIN_SUPPORTED
        if self.engine == "postgresql":
            return self.release >= POSTGRESQL_MIN_SUPPORTED
        return False

    @property
    def incompatibility(self) -> str:
        """Why ``supported`` is False, in a sentence meant for the user."""
        if self.supported:
            return ""
        named = self.release_name or self.version or str(self.release)
        if self.engine == "mssql":
            return (
                f"SQL Server {named} cannot be paged here: paging uses "
                "OFFSET ... FETCH NEXT, which arrived in SQL Server 2012."
            )
        if self.engine == "mysql":
            if self.mariadb:
                return (
                    "this server reports MariaDB, whose version numbers do not line up "
                    "with MySQL's; it is not a supported backend for this proxy yet."
                )
            return (
                f"MySQL {named} is too old: CTEs and window functions, which the plan "
                "builder uses, arrived in 8.0."
            )
        return f"PostgreSQL {named} is too old to be served here."


class ConnectConfig(Protocol):
    """The slice of ``DatabaseConfig`` this seam reads.

    ``schema_name`` is the field the relational projects add to ``DatabaseConfig``;
    until it lands, ``options["schema"]`` carries the same meaning, and
    :func:`configured_schema` is the only place that knows the precedence — a config
    may supply either, neither, or both.
    """

    host: Optional[str]
    port: Optional[int]
    database_id: Optional[str]
    username: Optional[str]
    password: Optional[str]
    use_tls: bool
    options: Mapping[str, object]


class ProbedConnection(Protocol):
    """The one call the version probe and the statistics reader make.

    ``sqlalchemy.Connection`` satisfies it; a test double returns rows as sequences
    and records the statement, which is why this is a protocol and not an engine
    import.
    """

    def execute(
        self, statement: object, parameters: Optional[Mapping[str, object]] = None
    ) -> Iterable[Sequence[object]]: ...


def configured_schema(config: ConnectConfig, default: Optional[str]) -> Optional[str]:
    """The schema a name lives in: the field, then the option, then the engine default."""
    for candidate in (getattr(config, "schema_name", None), _option(config, SCHEMA_OPTION)):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return default


def option_value(config: ConnectConfig, name: str, default: object) -> object:
    """``config.options[name]``, or ``default`` when it is absent or blank."""
    value = _option(config, name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return value


def _option(config: ConnectConfig, name: str) -> object:
    options = getattr(config, "options", None) or {}
    try:
        return options.get(name)
    except AttributeError:
        return None


def timeout_seconds(config: ConnectConfig, name: str, default: int) -> int:
    """A positive timeout in seconds, or the default.

    Zero and negative are not passed through: to one client that means "no limit"
    and to another "fail immediately", and an unbounded read query is precisely the
    failure this option exists to prevent.
    """
    raw = option_value(config, name, None)
    if raw is None:
        return default
    try:
        seconds = int(float(str(raw)))
    except (TypeError, ValueError):
        return default
    return seconds if seconds > 0 else default


def pick_isolation(
    config: ConnectConfig,
    engine_name: str,
    accepted: Sequence[str],
    default: Optional[str],
) -> Optional[str]:
    """``options["isolation_level"]`` when this engine really has that level.

    An unknown name raises rather than being dropped. A person who set an isolation
    level believes it is in effect, and what they believe changes what the rows on
    their canvas mean.
    """
    raw = option_value(config, ISOLATION_OPTION, None)
    if raw is None:
        return default
    want = re.sub(r"[\s_]+", " ", str(raw).strip().upper())
    if want not in accepted:
        raise ValueError(
            f"{engine_name} has no isolation level {want!r}. It offers: "
            + ", ".join(sorted(accepted))
            + "."
        )
    return want


@dataclass(frozen=True)
class DeclaredType:
    """A column type as the server spelled it, taken apart."""

    base: str
    precision: Optional[int] = None
    scale: Optional[int] = None
    unsigned: bool = False
    array: int = 0


def parse_declared_type(
    db_type: str, *, precision: Optional[int] = None, scale: Optional[int] = None
) -> DeclaredType:
    """``nvarchar(200)``, ``numeric(10,2) unsigned``, ``text[]`` -> base + args.

    Only the parts a vocabulary decision needs are kept. Nothing is discarded: an
    unrecognised type is still a column, and it becomes ``STRING`` rather than
    absence.
    """
    raw = re.sub(r"\s+", " ", str(db_type or "").strip().lower())
    array = 0
    while raw.endswith("[]"):
        array += 1
        raw = raw[:-2].rstrip()
    args: Sequence[str] = ()
    match = re.search(r"\(([^()]*)\)\s*$", raw)
    if match:
        args = tuple(piece.strip() for piece in match.group(1).split(","))
        raw = raw[: match.start()].rstrip()
    unsigned = "unsigned" in raw
    # Only the modifiers that are never part of a type name go; ``character
    # varying`` and ``double precision`` stay whole and are looked up as written,
    # because collapsing them onto a stem is how a lookup starts inventing names.
    raw = re.sub(r"\b(unsigned|signed|zerofill)\b", " ", raw)
    raw = re.sub(r"\b(without time zone|with time zone|local time zone)\b", " ", raw)
    base = re.sub(r"\s+", " ", raw).strip()
    return DeclaredType(
        base=base,
        precision=_first_int(precision, args, 0),
        scale=_first_int(scale, args, 1),
        unsigned=unsigned,
        array=array,
    )


def _first_int(explicit: Optional[int], args: Sequence[str], index: int) -> Optional[int]:
    if explicit is not None:
        return int(explicit)
    if len(args) > index:
        try:
            return int(args[index])
        except ValueError:
            # ``enum('a','b')`` answers here: an argument that is not a number is
            # not a precision, and guessing one would be worse than having none.
            return None
    return None


def cast_sqlalchemy_type(cast: str) -> Optional[TypeEngine]:
    """``"DECIMAL(38, 10)"`` -> ``DECIMAL(38, 10)``, so both spellings of a cast agree.

    The compiler lane builds statements through SQLAlchemy and the policy in this
    module speaks SQL type names; one parse, shared by both, is what keeps them from
    drifting apart. ``test_cast_names_round_trip_through_sqlalchemy`` asserts the two
    render identically, which is why this returns ``DECIMAL`` and not its ``Numeric``
    parent: on the mssql dialect ``Numeric`` renders ``NUMERIC``, a synonym to the
    server and a difference to anyone comparing the emitted SQL against a test.
    """
    match = re.match(r"^\s*dec(?:i)?mal\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", str(cast), re.I)
    if not match:
        return None
    precision = int(match.group(1))
    scale = int(match.group(2) or 0)
    from sqlalchemy import DECIMAL

    return DECIMAL(precision, scale, asdecimal=True)


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
    def column_type(self, declared_type: Optional[str]) -> TypeEngine:
        """A SQLAlchemy type for a declared column type, so a parameter is bound as
        the column's own type.

        This is the seam's answer to the bridge's ``param_type_for``: pyodbc sent
        every Python string as ``nvarchar``, and comparing one against a ``varchar``
        or ``bigint`` column made SQL Server convert *the column*, turning an index
        lookup into a full scan with nothing in the output to say so. SQLAlchemy
        binds typed parameters, so the remaining job is to hand it the real type —
        which is why ``normalise_type`` loses the distinction and ``column_type``
        must not.
        """

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


_REGISTRY: Dict[str, EngineSpelling] = {}
_LOADED = False


def register(spelling: EngineSpelling) -> EngineSpelling:
    """Add one engine spelling under its own ``name``."""
    _REGISTRY[spelling.name] = spelling
    return spelling


def engines_for(database_type: str) -> EngineSpelling:
    """The spelling for a database type, or :class:`UnknownEngine`.

    The engine package is imported on first use so this module never depends on it
    at import time, and a type with no case is named instead of guessing.
    """
    _ensure_loaded()
    key = str(database_type or "").strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise UnknownEngine(
            f"no relational engine spelling is registered for {database_type!r}. "
            "Registered: " + ", ".join(registered_engines()) + "."
        ) from None


def registered_engines() -> Tuple[str, ...]:
    _ensure_loaded()
    return tuple(sorted(_REGISTRY))


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import engines  # noqa: F401  (importing the package registers every engine)
