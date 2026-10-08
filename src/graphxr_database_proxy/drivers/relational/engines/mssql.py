"""SQL Server: the engine where the wrong answer is usually a silent one.

Three of this engine's rules exist because SQL Server will do something plausible
and be wrong: an ``AVG`` over an integer column answers with integer division, an
``nvarchar`` parameter compared against a ``varchar`` column converts *the column*
and the index goes unused, and ``ARITHABORT`` off puts the same query in a
different plan-cache entry than SSMS does — which is the whole of "fast when I test
it in the studio, slow from the app".
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import URL
from sqlalchemy.dialects import mssql as sa_types
from sqlalchemy.types import TypeEngine

from .. import ids
from ..engine import (
    CONNECT_ARGS_OPTION,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    MSSQL_FANOUT_CHUNK,
    MSSQL_INLINE_ID_MAX,
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

#: The DBAPI token. ``pyodbc`` is *not* the default here, and not by preference:
#: pyodbc 5.2.0 ships no cp314 wheel while this project's venv is CPython 3.14.2, so
#: it cannot be installed in the environment the proxy is built and tested in, and
#: pymssql 2.4.2 can. A site whose FreeTDS is unhappy asks for the other driver with
#: ``options["dbapi"] = "pyodbc"``; nothing else here cares, because the statement
#: text comes from the ``mssql`` dialect either way.
DEFAULT_DBAPI = "pymssql"

#: Kept from the bridge's release table: a bare ``16`` means nothing to a person
#: being told their server is too old to page.
RELEASE_NAMES = {
    9: "2005",
    10: "2008 / 2008 R2",
    11: "2012",
    12: "2014",
    13: "2016",
    14: "2017",
    15: "2019",
    16: "2022",
    17: "2025",
}

#: The bridge's session settings, every line still preventing a named failure.
#: ``ANSI_NULLS``/``ANSI_WARNINGS``/``CONCAT_NULL_YIELDS_NULL`` change how NULL
#: compares and concatenates, so a query compiled with them off is a different
#: query. ``QUOTED_IDENTIFIER`` off breaks every bracketed identifier we emit.
#: ``ARITHABORT`` is the plan-cache one. ``LOCK_TIMEOUT`` fails a blocked read in
#: seconds instead of leaving a browser canvas spinning until something upstream
#: gives up. The isolation level is *not* here: it goes through
#: ``execution_options``, which resets it when the connection returns to the pool.
SESSION_STATEMENTS: Tuple[str, ...] = (
    "SET ANSI_NULLS ON",
    "SET QUOTED_IDENTIFIER ON",
    "SET ANSI_WARNINGS ON",
    "SET ANSI_PADDING ON",
    "SET CONCAT_NULL_YIELDS_NULL ON",
    "SET ARITHABORT ON",
    "SET LOCK_TIMEOUT 5000",
)

#: ``SNAPSHOT`` is listed because SQL Server has it and a person may ask for it; it
#: needs ``ALLOW_SNAPSHOT_ISOLATION`` on the database, which the server reports
#: itself when it is not enabled.
ISOLATION_LEVELS: Tuple[str, ...] = (
    "AUTOCOMMIT",
    "READ UNCOMMITTED",
    "READ COMMITTED",
    "REPEATABLE READ",
    "SERIALIZABLE",
    "SNAPSHOT",
)

#: ``GROUP BY``/``ORDER BY`` refuse these outright.
LOB_TYPES = frozenset({"text", "ntext", "image", "xml", "sql_variant"})
#: ...and an ``nvarchar(max)`` is refused for the same reason. ``varbinary(max)`` is
#: left alone: no character cast would honestly describe it.
WIDE_CHAR_TYPES = frozenset({"varchar", "nvarchar", "char", "nchar"})
#: ``bit`` is in here because ``AVG``/``SUM`` over it are integer operations too.
INTEGER_TYPES = frozenset({"tinyint", "smallint", "int", "integer", "bigint", "bit"})
EXACT_DECIMAL_TYPES = frozenset({"decimal", "dec", "numeric", "money", "smallmoney"})
CHARACTER_TYPES = frozenset(
    {"char", "varchar", "nchar", "nvarchar", "text", "ntext", "sysname"}
)

#: These cannot carry ``<Label>:<key>`` because no unique index can be built on
#: them, and a key that cannot be enforced mints the same id for different rows:
#: ``text``/``ntext``/``image`` are non-indexable, ``xml`` indexes only through a
#: secondary XML index, ``geography``/``geometry`` index spatially but do not
#: compare for equality, and ``rowversion`` changes on every write, which would
#: rename the node while it sits on the canvas.
UNKEYABLE_TYPES = frozenset(
    {"text", "ntext", "image", "xml", "sql_variant", "geography", "geometry", "rowversion"}
)

_TYPE_TABLE: Dict[str, str] = {
    "tinyint": "INT32",
    "smallint": "INT32",
    "int": "INT32",
    "integer": "INT32",
    "bigint": "INT64",
    "real": "DOUBLE",
    "float": "DOUBLE",
    "decimal": "DECIMAL",
    "dec": "DECIMAL",
    "numeric": "DECIMAL",
    "money": "DECIMAL",
    "smallmoney": "DECIMAL",
    "bit": "BOOL",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
    "datetimeoffset": "TIMESTAMP",
    # ``time`` has no slot in the vocabulary: it reaches the canvas as text, which is
    # what the converter emits for it anyway.
    "time": "STRING",
    "char": "STRING",
    "nchar": "STRING",
    "varchar": "STRING",
    "nvarchar": "STRING",
    "sysname": "STRING",
    "text": "STRING",
    "ntext": "STRING",
    "uniqueidentifier": "STRING",
    "hierarchyid": "STRING",
    "geography": "STRING",
    "geometry": "STRING",
    "xml": "STRING",
    "sql_variant": "STRING",
    "binary": "BYTES",
    "varbinary": "BYTES",
    "image": "BYTES",
    # SQL Server's ``timestamp`` is the row version — a binary counter, not a date.
    # Legacy schemas use it as a modification marker, and reading it as a date would
    # put something on the canvas that is not a point in time.
    "timestamp": "BYTES",
    "rowversion": "BYTES",
}

#: ``varchar`` and ``nvarchar`` stay distinct, deliberately: this map is what a
#: bound parameter is typed with, and collapsing the two is the bug below.
_COLUMN_TYPES: Dict[str, TypeEngine] = {
    "tinyint": sa_types.TINYINT(),
    "smallint": sa_types.SMALLINT(),
    "int": sa_types.INTEGER(),
    "integer": sa_types.INTEGER(),
    "bigint": sa_types.BIGINT(),
    "bit": sa_types.BIT(),
    "real": sa_types.REAL(),
    "float": sa_types.FLOAT(),
    "decimal": sa_types.DECIMAL(),
    "dec": sa_types.DECIMAL(),
    "numeric": sa_types.NUMERIC(),
    "money": sa_types.MONEY(),
    "smallmoney": sa_types.SMALLMONEY(),
    "date": sa_types.DATE(),
    "datetime": sa_types.DATETIME(),
    "datetime2": sa_types.DATETIME2(),
    "smalldatetime": sa_types.SMALLDATETIME(),
    "datetimeoffset": sa_types.DATETIMEOFFSET(),
    "time": sa_types.TIME(),
    "char": sa_types.CHAR(),
    "varchar": sa_types.VARCHAR(),
    "nchar": sa_types.NCHAR(),
    "nvarchar": sa_types.NVARCHAR(),
    "text": sa_types.TEXT(),
    "ntext": sa_types.NTEXT(),
    "uniqueidentifier": sa_types.UNIQUEIDENTIFIER(),
    "binary": sa_types.BINARY(),
    "varbinary": sa_types.VARBINARY(),
    "image": sa_types.IMAGE(),
    "rowversion": sa_types.ROWVERSION(),
    "timestamp": sa_types.TIMESTAMP(),
}

_MAX_ARG = re.compile(r"\(\s*max\s*\)", re.I)


class MSSQLEngine(EngineSpelling):
    """SQL Server and Azure SQL."""

    name = "mssql"
    dbapi = DEFAULT_DBAPI
    pip_extra = "pymssql"
    default_port = 1433
    default_schema = "dbo"
    #: T-SQL has no regex operator at all, so ``=~`` is refused by name rather than
    #: answered with an empty canvas: CONTAINS, STARTS WITH and ENDS WITH do exist.
    supports_regex = False
    #: The default database collation is case-insensitive, so ``Order`` and ``order``
    #: are one table — which is why a mapping listing both is a collision, not two
    #: labels.
    name_case_rule = "insensitive"
    max_identifier_length = 128

    # -- connection --------------------------------------------------------

    def url(self, config: ConnectConfig, *, password: Optional[str] = None) -> URL:
        """``mssql+pymssql``, the password held only inside the URL object.

        A named instance is found by the SQL Browser rather than by a port, so the
        two addressing modes cannot both be honoured: stating an instance wins and
        the port is dropped instead of quietly competing with it.
        """
        driver = str(option_value(config, "dbapi", DEFAULT_DBAPI) or DEFAULT_DBAPI)
        host = str(config.host or "localhost")
        port: Optional[int] = int(config.port or self.default_port)
        instance = option_value(config, "instance", None)
        if isinstance(instance, str) and instance.strip():
            host = f"{host}\\{instance.strip()}"
            port = None
        query: Dict[str, object] = {}
        tds = option_value(config, "tds_version", None)
        if isinstance(tds, str) and tds.strip():
            query["tds_version"] = tds.strip()
        extra = option_value(config, URL_QUERY_OPTION, {})
        if isinstance(extra, Mapping):
            query.update({str(key): value for key, value in extra.items()})
        return URL.create(
            f"mssql+{driver}",
            username=config.username or "",
            password=password if password is not None else (config.password or None),
            host=host,
            port=port,
            database=str(config.database_id or ""),
            query={key: str(value) for key, value in query.items() if value not in (None, "")},
        )

    def connect_args(self, config: ConnectConfig) -> Dict[str, object]:
        """Timeouts and charset. No password: it lives in the URL object only.

        ``use_tls`` is deliberately *not* translated into a keyword. pymssql speaks
        TDS through FreeTDS, which negotiates encryption from its own configuration
        and the server's offer; there is no per-connection ``encrypt`` flag to set,
        and inventing one would either be ignored or fail at connect. A site that
        needs it says so through ``options["connect_args"]`` (``conn_properties``,
        ``tds_version``) or ``options["url_query"]``, both merged here and passed
        through untouched — visible, rather than a checkbox that does nothing.
        """
        args: Dict[str, object] = {
            "login_timeout": timeout_seconds(
                config, "login_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
            "timeout": timeout_seconds(
                config, QUERY_TIMEOUT_OPTION, DEFAULT_QUERY_TIMEOUT_SECONDS
            ),
            "charset": str(option_value(config, "charset", "UTF-8")),
            "appname": str(option_value(config, "appname", "graphxr-proxy")),
        }
        extra = option_value(config, CONNECT_ARGS_OPTION, {})
        if isinstance(extra, Mapping):
            args.update({str(key): value for key, value in extra.items()})
        return args

    def session_statements(self) -> Tuple[str, ...]:
        return SESSION_STATEMENTS

    def isolation(self, config: ConnectConfig) -> Optional[str]:
        """Default ``READ UNCOMMITTED``, for the bridge's reason.

        An analyst's expand must neither wait for someone editing records nor block
        them. The cost is reading a half-written row, which for drawing a picture is
        acceptable; freezing an investigator's session is not. Someone who wants
        committed reads sets ``options["isolation_level"]``.
        """
        return pick_isolation(config, self.name, ISOLATION_LEVELS, "READ UNCOMMITTED")

    # -- types -------------------------------------------------------------

    def type_table(self) -> Mapping[str, str]:
        return _TYPE_TABLE

    def refine_type(self, declared: DeclaredType) -> Optional[str]:
        # No argument decides a SQL Server vocabulary slot: ``float(50)`` and
        # ``float(25)`` are both DOUBLE, and ``(max)`` only matters for the casts.
        return None

    def column_type(self, declared_type: Optional[str]) -> Optional[TypeEngine]:
        """Bind as the column's own type, which is the point of the whole exercise.

        A text parameter against a ``varchar`` column and an ``nvarchar`` parameter
        against the same column are *different predicates* to SQL Server: the second
        converts the column, and a converted column cannot use its index — the
        difference between 20 ms and 20 s on an expand, with nothing in the output to
        say so. So ``varchar`` must not arrive as Unicode and ``nvarchar`` must not
        arrive as plain varchar, which is why this map keeps both spellings and
        ``normalise_type`` does not.

        None means this map has no name for that type. That is the honest answer for the
        types SQL Server gives no equality at all — ``xml``, the spatial types,
        ``sql_variant`` — where a text parameter is not a slower plan but the server's own
        type error.
        """
        declared = parse_declared_type(declared_type or "")
        if declared.array:
            # SQL Server has no array column type either; the name that gets here is the
            # one a hand-written document stated. Refused by name, not read as its element
            # — see ``array_binding``.
            return self.array_binding(declared)
        return _COLUMN_TYPES.get(declared.base)

    def unkeyable_types(self) -> frozenset:
        return UNKEYABLE_TYPES

    # -- casts -------------------------------------------------------------

    def agg_cast(self, function: str, column_type: Optional[str]) -> Optional[str]:
        """``DECIMAL(38, 10)`` for an average that would truncate, ``DECIMAL(38, 0)``
        for a sum that would overflow, and nothing else.

        SQLAlchemy 2.1.3 emits ``avg([Order].n)`` bare on mssql: the compiler does not
        know that SQL Server's ``AVG`` over an integer or a scale-0 exact numeric
        returns a value of the same integer-ish shape it went in as, so ``avg(1,2,2)``
        answers ``1``. ``SUM`` over an integer widens only to ``bigint`` and then
        overflows on a big ledger. ``min``/``max``/``count`` are left alone — they
        preserve the type, and casting around ``count`` changes what it counts.
        """
        fn = str(function or "").strip().lower()
        declared = parse_declared_type(column_type or "")
        if declared.base in INTEGER_TYPES:
            if fn == "avg":
                return "DECIMAL(38, 10)"
            if fn == "sum":
                return "DECIMAL(38, 0)"
            return None
        if declared.base in EXACT_DECIMAL_TYPES and declared.scale in (None, 0) and fn == "avg":
            return "DECIMAL(38, 10)"
        return None

    def group_key_cast(self, column_type: Optional[str]) -> Optional[str]:
        """Narrow an unbounded LOB to 450 characters so it can be grouped at all.

        450 is the index-key size the cast has to fit, not a rounding choice.
        """
        stated = str(column_type or "")
        declared = parse_declared_type(stated)
        if declared.base in LOB_TYPES:
            return "NVARCHAR(450)"
        if declared.base in WIDE_CHAR_TYPES and _MAX_ARG.search(stated):
            return "NVARCHAR(450)"
        return None

    def like_cast(self, column_type: Optional[str]) -> Optional[str]:
        """Only cast a column that is not already character data.

        Wrapping an indexed ``varchar`` in ``CAST`` in order to compare it is the same
        index-killing move as binding the wrong parameter type, so the answer for a
        character column is always None.
        """
        declared = parse_declared_type(column_type or "")
        if not declared.base or declared.base in CHARACTER_TYPES:
            return None
        return "NVARCHAR(4000)"

    # -- server ------------------------------------------------------------

    def probe_sql(self) -> str:
        """Version, compatibility level and writability in one SELECT.

        Two numbers, not one: ``OPENJSON`` needs a 2016 engine *and* a database at
        compatibility level 130, and a database restored from an older server keeps
        its old level, so a modern engine on its own is not enough.
        """
        return (
            "SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int) AS major,"
            " CAST(SERVERPROPERTY('ProductMinorVersion') AS int) AS minor,"
            " CAST(SERVERPROPERTY('ProductBuild') AS int) AS build,"
            " CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)) AS version,"
            " CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,"
            " (SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME()) AS compat,"
            " CASE WHEN DATABASEPROPERTYEX(DB_NAME(), 'Updateability') = 'READ_ONLY'"
            " THEN 1 ELSE 0 END AS read_only"
        )

    def server_from_row(self, row: Sequence[object]) -> ServerInfo:
        major = _as_int(row[0])
        version = str(row[3] or "")
        edition = str(row[4] or "")
        named = RELEASE_NAMES.get(major, f"major version {major}")
        if "azure" in edition.lower():
            named = "Azure SQL"
        return ServerInfo(
            engine=self.name,
            version=version,
            major=major,
            minor=_as_int(row[1]),
            build=_as_int(row[2]),
            compat_level=_as_int(row[5]),
            edition=edition,
            release_name=named,
            read_only_server=bool(_as_int(row[6])),
            # The collation is a per-database fact one probe cannot see, and the
            # default is case-insensitive; ``name_case_rule`` is the answer used.
            names_case_insensitive=True,
        )

    def counts_statement(
        self, schema: Optional[str], names: Sequence[str]
    ) -> Tuple[str, Dict[str, object]]:
        """``sys.partitions.rows``: metadata, so nothing here ever scans.

        ``dm_db_partition_stats`` says the same thing but needs VIEW SERVER STATE,
        which a read-only login routinely does not have. Both are estimates, and a
        table with no partition row (a closed columnstore group mid-rebuild) simply
        does not appear. ``index_id`` 0/1 is the heap or the clustered leaf, so a row
        is counted once whatever the table's indexing.
        """
        wanted = list(names)
        placeholders = ", ".join(f":n{index}" for index in range(len(wanted)))
        where = f"o.name IN ({placeholders})" if wanted else "1 = 0"
        binds: Dict[str, object] = {f"n{index}": name for index, name in enumerate(wanted)}
        if schema:
            where += " AND SCHEMA_NAME(o.schema_id) = :schema"
            binds["schema"] = schema
        sql = (
            "SELECT o.name AS table_name, SUM(p.rows) AS row_count"
            " FROM sys.partitions p JOIN sys.objects o ON o.object_id = p.object_id"
            f" WHERE p.index_id IN (0, 1) AND o.type IN ('U', 'V') AND {where}"
            " GROUP BY o.schema_id, o.name"
        )
        return sql, binds

    # -- id lists ----------------------------------------------------------

    def id_list_strategy(self, info: ServerInfo, size: int) -> str:
        """``inline`` to 2 000, one ``bulk`` parameter past that, ``fanout`` when the
        server cannot carry one.

        2 100 parameters is a hard engine ceiling and GraphXR hands over a selection
        of tens of thousands of ids routinely, so the ladder is about fitting rather
        than tuning. A list past the inline band goes over as a single ``nvarchar``
        JSON document shredded by ``OPENJSON``, whose ``WITH`` clause declares the
        column type — so there is no hidden text-to-number conversion to stop the
        join using an index, and JSON has no delimiter a record key could contain.
        Below the 2016 / compat-130 bar the statement is chopped into branches
        instead, which is correct but reads as several queries and pages across them
        only best-effort.
        """
        if size <= MSSQL_INLINE_ID_MAX:
            return "inline"
        return "bulk" if info.supports_openjson else "fanout"

    def bulk_id_form(self, info: ServerInfo) -> str:
        return "openjson" if info.supports_openjson else "placeholders"

    def json_document_type(self) -> str:
        # SQL Server has no JSON type: the document crosses as Unicode text, and a
        # selection of tens of thousands of ids is longer than any bounded column.
        return "nvarchar(max)"

    def shredded_column_type(self, declared_type: Optional[str]) -> str:
        # Whole keys shred as ``bigint`` and text keys as ``nvarchar(450)``, the
        # index-key width ``group_key_cast`` shares: a value column SQL Server cannot
        # compare against an index key without converting one side is the conversion
        # the ``WITH`` clause was written to stop.
        return "bigint" if ids.is_integral_type(declared_type) else "nvarchar(450)"

    def fanout_chunk_size(self, info: ServerInfo) -> int:
        return MSSQL_FANOUT_CHUNK

    # -- names -------------------------------------------------------------

    def identifier_error(self, name: str) -> str:
        """The prefixes SQL Server reads as something else entirely."""
        candidate = str(name or "")
        if candidate.startswith("##"):
            return "the name starts with ##, which SQL Server reads as a global temp table"
        if candidate.startswith("#"):
            return "the name starts with #, which SQL Server reads as a temp table"
        if candidate.startswith("@"):
            return "the name starts with @, which SQL Server reads as a variable"
        return super().identifier_error(name)


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
