"""PostgreSQL: the engine where the danger is a name that quietly becomes another.

Unquoted identifiers fold to **lower case** here, so a mapping that names ``Order``
and one that names ``order`` reach two different tables — and a statement that
forgets to quote reaches whichever one the search path resolves. Everything this
module emits is quoted by SQLAlchemy's compiler, and the mapping validator refuses
a name that would need quoting anyway, so the two rules meet in the middle instead
of one of them holding alone.

The other difference worth stating out loud: PostgreSQL has no uncommitted reads.
It accepts the words ``READ UNCOMMITTED`` and means READ COMMITTED, which is a
silent substitution of exactly the kind this seam exists to prevent, so asking for
it here is refused by name rather than honoured.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy import URL
from sqlalchemy.dialects import postgresql as sa_types
from sqlalchemy.types import TypeEngine

from ..engine import (
    CONNECT_ARGS_OPTION,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    POSTGRESQL_INLINE_ID_MAX,
    POSTGRESQL_REFUSED_ISOLATION,
    QUERY_TIMEOUT_OPTION,
    URL_QUERY_OPTION,
    ConnectConfig,
    DeclaredType,
    EngineSpelling,
    ServerInfo,
    option_value,
    parse_declared_type,
    pick_isolation,
    timeout_seconds,
)

DEFAULT_DBAPI = "psycopg"

#: The three-character answer to "which driver do I install": psycopg 3 with its
#: bundled libpq, because psycopg2 needs the system library and a Docker image that
#: does not have it fails at connect rather than at install.
PIP_EXTRA = "psycopg[binary]"

#: Every session may set this one for itself, which is what makes it usable here: a
#: hot standby sets it anyway, and a primary sets nothing the person did not ask for.
SESSION_STATEMENTS: Tuple[str, ...] = ("SET default_transaction_read_only = on",)

ISOLATION_LEVELS: Tuple[str, ...] = (
    "AUTOCOMMIT",
    "READ COMMITTED",
    "REPEATABLE READ",
    "SERIALIZABLE",
)

#: Multi-word spellings are looked up whole, so ``character varying`` and
#: ``double precision`` are their own keys rather than a stem a reader has to guess.
_TYPE_TABLE: Dict[str, str] = {
    "int2": "INT32",
    "smallint": "INT32",
    "smallserial": "INT32",
    "int4": "INT32",
    "integer": "INT32",
    "int": "INT32",
    "serial": "INT32",
    "int8": "INT64",
    "bigint": "INT64",
    "bigserial": "INT64",
    "oid": "INT64",
    "real": "DOUBLE",
    "float4": "DOUBLE",
    "double precision": "DOUBLE",
    "float8": "DOUBLE",
    "float": "DOUBLE",
    "numeric": "DECIMAL",
    "decimal": "DECIMAL",
    "money": "DECIMAL",
    "boolean": "BOOL",
    "bool": "BOOL",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "timestamptz": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
    "time": "STRING",
    "timetz": "STRING",
    "interval": "STRING",
    "character": "STRING",
    "char": "STRING",
    "character varying": "STRING",
    "varchar": "STRING",
    "text": "STRING",
    "name": "STRING",
    "citext": "STRING",
    "uuid": "STRING",
    "inet": "STRING",
    "cidr": "STRING",
    "macaddr": "STRING",
    "json": "JSON",
    "jsonb": "JSON",
    "xml": "STRING",
    "bytea": "BYTES",
    "bit": "BYTES",
    "bit varying": "BYTES",
    "varbit": "BYTES",
    "tsvector": "STRING",
    "tsquery": "STRING",
    "point": "STRING",
    "line": "STRING",
    "lseg": "STRING",
    "box": "STRING",
    "path": "STRING",
    "polygon": "STRING",
    "circle": "STRING",
}

#: Same reason as on SQL Server, and the trap has a PostgreSQL flavour: ``uuid``
#: compares and indexes fine, ``inet``/``json``/``xml`` reach the canvas as text and
#: make poor identity columns, and a large object has no stable text form.
UNKEYABLE_TYPES = frozenset({"bytea", "json", "jsonb", "xml", "tsvector", "tsquery"})

_COLUMN_TYPES: Dict[str, TypeEngine] = {
    "int2": sa_types.SMALLINT(),
    "smallint": sa_types.SMALLINT(),
    "int4": sa_types.INTEGER(),
    "integer": sa_types.INTEGER(),
    "int": sa_types.INTEGER(),
    "serial": sa_types.INTEGER(),
    "smallserial": sa_types.SMALLINT(),
    "int8": sa_types.BIGINT(),
    "bigint": sa_types.BIGINT(),
    "bigserial": sa_types.BIGINT(),
    "oid": sa_types.OID(),
    "real": sa_types.REAL(),
    "float4": sa_types.REAL(),
    "double precision": sa_types.DOUBLE_PRECISION(),
    "float8": sa_types.DOUBLE_PRECISION(),
    "float": sa_types.DOUBLE_PRECISION(),
    "numeric": sa_types.NUMERIC(),
    "decimal": sa_types.NUMERIC(),
    "money": sa_types.MONEY(),
    "boolean": sa_types.BOOLEAN(),
    "bool": sa_types.BOOLEAN(),
    "date": sa_types.DATE(),
    "timestamp": sa_types.TIMESTAMP(),
    "timestamptz": sa_types.TIMESTAMP(timezone=True),
    "timestamp without time zone": sa_types.TIMESTAMP(),
    "timestamp with time zone": sa_types.TIMESTAMP(timezone=True),
    "time": sa_types.TIME(),
    "timetz": sa_types.TIME(timezone=True),
    "interval": sa_types.INTERVAL(),
    "character": sa_types.CHAR(),
    "char": sa_types.CHAR(),
    "character varying": sa_types.VARCHAR(),
    "varchar": sa_types.VARCHAR(),
    "text": sa_types.TEXT(),
    # ``name`` is a fixed-width system type with no SQLAlchemy class of its own, and
    # there is no XML type to bind against either: text describes both faithfully.
    "name": sa.Text(),
    "citext": sa_types.CITEXT(),
    "uuid": sa_types.UUID(),
    "inet": sa_types.INET(),
    "cidr": sa_types.CIDR(),
    "macaddr": sa_types.MACADDR(),
    "json": sa_types.JSON(),
    "jsonb": sa_types.JSONB(),
    "xml": sa.Text(),
    "bytea": sa_types.BYTEA(),
    "bit": sa_types.BIT(),
    "bit varying": sa_types.BIT(),
    "varbit": sa_types.BIT(),
}

_VERSION_PIECES = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class PostgreSQLEngine(EngineSpelling):
    """PostgreSQL, and the SQL/PGQ-compatible forks that answer ``version()`` alike."""

    name = "postgresql"
    dbapi = DEFAULT_DBAPI
    pip_extra = PIP_EXTRA
    default_port = 5432
    default_schema = "public"
    #: ``~`` and ``!~`` are real operators on ``text``, and they use an expression
    #: index when one exists.
    supports_regex = True
    #: Unquoted folds to lower case *and* quoting preserves case, so the two spells
    #: are two tables. Everything emitted here is quoted.
    name_case_rule = "fold-lower"
    #: A name longer than 63 **bytes** is truncated on creation, silently. Refusing
    #: it in the mapping document is the only place a person can still be told.
    max_identifier_length = 63

    # -- connection --------------------------------------------------------

    def url(self, config: ConnectConfig, *, password: Optional[str] = None) -> URL:
        """``postgresql+psycopg``, libpq keywords in the query, nothing else.

        ``sslmode`` is the one libpq knob that expresses TLS, so ``use_tls`` maps
        onto it and a site that needs ``verify-full`` with a CA says so through
        ``options["sslmode"]`` / ``options["sslrootcert"]`` rather than a boolean.
        """
        driver = str(option_value(config, "dbapi", DEFAULT_DBAPI) or DEFAULT_DBAPI)
        query: Dict[str, object] = {}
        sslmode = option_value(config, "sslmode", None)
        if isinstance(sslmode, str) and sslmode.strip():
            query["sslmode"] = sslmode.strip()
        elif config.use_tls:
            query["sslmode"] = "require"
        rootcert = option_value(config, "sslrootcert", None)
        if isinstance(rootcert, str) and rootcert.strip():
            query["sslrootcert"] = rootcert.strip()
            query.setdefault("sslmode", "verify-ca")
        appname = option_value(config, "application_name", None)
        if isinstance(appname, str) and appname.strip():
            query["application_name"] = appname.strip()
        # A per-connection default, so a runaway query ends on the server instead of
        # leaving a cursor open and a pooled connection held. ``SET LOCAL`` is the
        # per-transaction form and belongs to whichever statement needs it.
        timeout_ms = self._statement_timeout_ms(config)
        if timeout_ms:
            settings = [f"-c statement_timeout={timeout_ms}"]
            search_path = option_value(config, "search_path", None)
            if isinstance(search_path, str) and search_path.strip():
                settings.append(f"-c search_path={search_path.strip()}")
            query["options"] = " ".join(settings)
        extra = option_value(config, URL_QUERY_OPTION, {})
        if isinstance(extra, Mapping):
            query.update({str(key): value for key, value in extra.items()})
        return URL.create(
            f"postgresql+{driver}",
            username=config.username or "",
            password=password if password is not None else (config.password or None),
            host=str(config.host or "localhost"),
            port=int(config.port or self.default_port),
            database=str(config.database_id or ""),
            query={key: str(value) for key, value in query.items() if value not in (None, "")},
        )

    def _statement_timeout_ms(self, config: ConnectConfig) -> int:
        seconds = timeout_seconds(config, QUERY_TIMEOUT_OPTION, DEFAULT_QUERY_TIMEOUT_SECONDS)
        stated = option_value(config, "statement_timeout_ms", None)
        if stated is not None:
            try:
                return max(0, int(float(str(stated))))
            except (TypeError, ValueError):
                return seconds * 1000
        return seconds * 1000

    def connect_args(self, config: ConnectConfig) -> Dict[str, object]:
        """``connect_timeout`` only; everything else is a libpq keyword in the URL.

        No password here: it belongs to the URL object, whose ``repr`` masks it, so a
        dict of connect arguments can be logged. ``connect_timeout`` is in seconds and
        takes a float, and it is the one setting that turns "the host is on a network
        that has gone away" into a message inside seconds instead of the operating
        system's own.
        """
        args: Dict[str, object] = {
            "connect_timeout": timeout_seconds(
                config, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
            )
        }
        extra = option_value(config, CONNECT_ARGS_OPTION, {})
        if isinstance(extra, Mapping):
            args.update({str(key): value for key, value in extra.items()})
        return args

    def session_statements(self) -> Tuple[str, ...]:
        return SESSION_STATEMENTS

    def isolation(self, config: ConnectConfig) -> Optional[str]:
        """None by default: the server's READ COMMITTED is the right reading of a
        graph canvas, and the read-only guarantee comes from the session statement,
        not from an isolation level.

        ``READ UNCOMMITTED`` is refused rather than passed through, because passing
        it through is what makes it a lie: PostgreSQL accepts the words and gives you
        READ COMMITTED, so a person who asked for dirty reads believes they got them.
        """
        want = option_value(config, "isolation_level", None)
        if want is not None:
            normalised = re.sub(r"[\s_]+", " ", str(want).strip().upper())
            if normalised == POSTGRESQL_REFUSED_ISOLATION:  # the alias, refused by name
                raise ValueError(
                    "postgresql has no READ UNCOMMITTED: the words are accepted as an "
                    "alias for READ COMMITTED, so asking for it does not get you "
                    "uncommitted reads. Leave isolation_level unset, or pick one of: "
                    + ", ".join(ISOLATION_LEVELS)
                    + "."
                )
        return pick_isolation(config, self.name, ISOLATION_LEVELS, None)

    # -- types -------------------------------------------------------------

    def type_table(self) -> Mapping[str, str]:
        return _TYPE_TABLE

    def refine_type(self, declared: DeclaredType) -> Optional[str]:
        # An ``enum`` is a string with a checklist attached; the canvas wants the
        # label, and the labels are what the schema panel will list anyway.
        if declared.base == "enum":
            return "STRING"
        return None

    def column_type(self, declared_type: Optional[str]) -> TypeEngine:
        """The declared type, so a parameter is sent as that type.

        The pyodbc story does not repeat itself here: psycopg 3 adapts a Python
        ``str`` to ``text`` and lets the server resolve it against the column, so a
        ``varchar`` comparison does not force a conversion. What is still got wrong
        when the type is *not* stated is the other direction — an untyped parameter
        against an ``int8``/``numeric`` key arrives as text and PostgreSQL then has to
        guess, which is how a ``bigint`` key ends up compared as text and loses its
        index scan. Same conclusion as the bridge reached by ``setinputsizes``: say
        the type.
        """
        declared = parse_declared_type(declared_type or "")
        # A user-defined enum has no built-in type to name; binding it as text is
        # what PostgreSQL compares by.
        return _COLUMN_TYPES.get(declared.base, sa.String())

    def unkeyable_types(self) -> frozenset:
        return UNKEYABLE_TYPES

    # -- casts -------------------------------------------------------------

    def agg_cast(self, function: str, column_type: Optional[str]) -> Optional[str]:
        """Always None, and that is the answer rather than an omission.

        ``avg`` over any numeric in PostgreSQL already returns ``numeric`` — no
        integer division to dodge — and ``sum(int4)`` returns ``bigint``,
        ``sum(int8)`` returns ``numeric``, so widening has already happened. A cast
        here would be decoration at best: applied to a ``text`` expression it changes
        what the aggregate means, and the bridge's mssql rule copied onto PostgreSQL
        is exactly the kind of flag that outlives the bug it was written for.
        """
        return None

    def group_key_cast(self, column_type: Optional[str]) -> Optional[str]:
        # ``text`` and ``bytea`` group fine; nothing needs narrowing.
        return None

    def like_cast(self, column_type: Optional[str]) -> Optional[str]:
        """None. ``LIKE`` needs ``text``, and casting a column to text in order to
        compare it stops a functional index on that column being usable."""
        return None

    # -- server ------------------------------------------------------------

    def probe_sql(self) -> str:
        """``server_version_num`` is the number, ``version()`` the sentence.

        The recovery flag is what makes a hot standby a different kind of server
        rather than a surprise: nothing here can write to it, and saying so at
        connect time beats a failed save later.
        """
        return (
            "SELECT current_setting('server_version') AS version,"
            " current_setting('server_version_num')::int AS version_num,"
            " version() AS full_version,"
            " pg_is_in_recovery() AS in_recovery,"
            " current_setting('default_transaction_read_only')::bool AS read_only"
        )

    def server_from_row(self, row: Sequence[object]) -> ServerInfo:
        version = str(row[0] or "")
        raw_number = row[1]
        major, minor, build = _split_version(version)
        try:
            # server_version_num is ``160400`` for 16.4: major * 10000 + minor since
            # 10.0, and ``90624`` for 9.6.24 before that, where the maths is
            # different. The printed string is the honest source for both.
            number = int(raw_number) if raw_number is not None else None
        except (TypeError, ValueError):
            number = None
        if number is not None and major >= 10:
            major = number // 10000
            minor = (number % 10000) // 100
        in_recovery = _as_bool(row[3])
        read_only = _as_bool(row[4])
        return ServerInfo(
            engine=self.name,
            version=version,
            major=major,
            minor=minor,
            build=build,
            edition=str(row[2] or ""),
            release_name=".".join(str(part) for part in (major, minor) + ((build,) if build else ())),
            read_only_server=in_recovery or read_only,
            names_case_insensitive=False,
        )

    def counts_statement(
        self, schema: Optional[str], names: Sequence[str]
    ) -> Tuple[str, Dict[str, object]]:
        """``pg_class.reltuples``: what the last vacuum or analyse counted.

        A number no one has counted is ``-1``, which is why the caller drops
        negatives instead of drawing a table as empty. Partitioned parents carry the
        parent's own count (zero rows live in them), so a partitioned table reads as
        small rather than lying about being large.

        ``reltuples`` is left as the server says it, including the ``-1`` that means
        "never counted": the shared caller drops negatives, so an uncounted table is
        absent rather than drawn as empty. ``GREATEST(…, 0)`` here would turn that
        honest unknown into a lie the schema panel would show as 0 rows.

        One name list, one array parameter: no per-name placeholder, so a page of
        names costs one bind.
        """
        binds: Dict[str, object] = {"names": list(names)}
        where = "c.relname = ANY(CAST(:names AS text[]))"
        if schema:
            where += " AND n.nspname = :schema"
            binds["schema"] = schema
        else:
            where += " AND n.nspname = current_schema()"
        sql = (
            "SELECT c.relname AS table_name, c.reltuples::bigint AS row_count"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'p', 'm') AND " + where
        )
        return sql, binds

    # -- id lists ----------------------------------------------------------

    def id_list_strategy(self, info: ServerInfo, size: int) -> str:
        """``inline`` for a handful, then one array parameter forever after.

        There is no parameter ceiling to dodge, so the crossover is about the plan a
        reader can recognise in a log; past it, ``= ANY($1)`` with one ``text[]``/
        ``bigint[]`` bind beats ten thousand placeholders on wire size and on parse
        time alike, and the planner gets the array's actual length at run time.
        """
        if size <= POSTGRESQL_INLINE_ID_MAX:
            return "inline"
        return "bulk"

    def bulk_id_form(self, info: ServerInfo) -> str:
        return "any"

    def json_document_type(self) -> str:
        # Never read here — ``= ANY`` carries the list itself — and ``json``, not
        # ``jsonb``, which would have the server re-encode a document it only parses.
        return "json"

    def fanout_chunk_size(self, info: ServerInfo) -> int:
        # Never used: ``= ANY`` carries a selection of any size in one parameter.
        return 0

    # -- names -------------------------------------------------------------

    def identifier_error(self, name: str) -> str:
        stated = str(name or "")
        if len(stated) > self.max_identifier_length:
            return (
                f"postgresql truncates a name to 63 bytes when it creates one, so "
                f"{stated!r} would be stored as {stated[:63]!r} and never match again"
            )
        return super().identifier_error(name)


def _split_version(version: str) -> Tuple[int, int, int]:
    match = _VERSION_PIECES.match(str(version or "").strip())
    if not match:
        return (0, 0, 0)
    parts = [int(piece) if piece else 0 for piece in match.groups()]
    major, minor, build = parts[0], parts[1], parts[2]
    if major >= 10:
        # 16.4 has no separate patch number in the printed version.
        return (major, minor, 0)
    return (major, minor, build)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("t", "true", "1", "yes", "on")
