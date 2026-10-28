"""MySQL / MariaDB: the engine that converts the *column* to match the parameter.

An ``INT`` compared against a ``VARCHAR`` column does not error here — MySQL turns
every value in that column into a number and compares, which cannot use the index
and, worse, quietly matches rows a strict engine would have rejected. The same
happens in reverse. So the two rules that matter are: bind every parameter as the
column's declared type, and treat a key column that MySQL can only index by prefix
as no key at all.

Charset is the other standing decision. ``utf8mb4`` is the real UTF-8; ``utf8`` is
MySQL's three-byte ``utf8mb3`` alias, in which an emoji is silently truncated to
``?``. Every comparison is also collation-aware, so ``café`` equals ``cafe`` under a
``_ci``/accent-insensitive collation — that is the server's rule, not ours, and it is
why a key lookup may legitimately return a row whose text is not byte-identical.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import URL
from sqlalchemy.dialects import mysql as sa_types
from sqlalchemy.types import TypeEngine

from .. import ids
from ..engine import (
    CONNECT_ARGS_OPTION,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    MYSQL_FANOUT_CHUNK,
    MYSQL_INLINE_ID_MAX,
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

DEFAULT_DBAPI = "pymysql"
#: The project spells this extra ``PyMySQL``; the module it imports is ``pymysql``.
PIP_EXTRA = "PyMySQL"

#: ``utf8`` is MySQL's three-byte ``utf8mb3`` alias, in which an emoji is silently
#: truncated to ``?``, so the four-byte charset is stated rather than inherited.
CHARSET = "utf8mb4"

ISOLATION_LEVELS: Tuple[str, ...] = (
    "AUTOCOMMIT",
    "READ UNCOMMITTED",
    "READ COMMITTED",
    "REPEATABLE READ",
    "SERIALIZABLE",
)

#: MySQL needs a prefix length to index these at all, and a ``UNIQUE KEY (col(20))``
#: enforces uniqueness of the first 20 characters only: two rows that differ past
#: that both load, and the ids minted from them collide.
UNKEYABLE_TYPES = frozenset(
    {
        "tinytext",
        "text",
        "mediumtext",
        "longtext",
        "tinyblob",
        "blob",
        "mediumblob",
        "longblob",
        "json",
        "point",
        "linestring",
        "polygon",
        "geometry",
        "geometrycollection",
        "multilinestring",
        "multipoint",
        "multipolygon",
    }
)

_TYPE_TABLE: Dict[str, str] = {
    "tinyint": "INT32",
    "bool": "BOOL",
    "boolean": "BOOL",
    "smallint": "INT32",
    "mediumint": "INT32",
    "int": "INT32",
    "integer": "INT32",
    "bigint": "INT64",
    "float": "DOUBLE",
    "real": "DOUBLE",
    "double": "DOUBLE",
    "double precision": "DOUBLE",
    "decimal": "DECIMAL",
    "numeric": "DECIMAL",
    "year": "INT32",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    # ``time`` has no vocabulary slot and reaches the canvas as text.
    "time": "STRING",
    "char": "STRING",
    "varchar": "STRING",
    "tinytext": "STRING",
    "text": "STRING",
    "mediumtext": "STRING",
    "longtext": "STRING",
    "enum": "STRING",
    "set": "STRING",
    "binary": "BYTES",
    "varbinary": "BYTES",
    "tinyblob": "BYTES",
    "blob": "BYTES",
    "mediumblob": "BYTES",
    "longblob": "BYTES",
    "json": "JSON",
    # Spatial columns come back as WKB bytes, not as text, whatever they look like in
    # a client that renders them.
    "point": "BYTES",
    "linestring": "BYTES",
    "polygon": "BYTES",
    "geometry": "BYTES",
    "geometrycollection": "BYTES",
    "multilinestring": "BYTES",
    "multipoint": "BYTES",
    "multipolygon": "BYTES",
}

_COLUMN_TYPES: Dict[str, TypeEngine] = {
    "tinyint": sa_types.TINYINT(),
    "smallint": sa_types.SMALLINT(),
    "mediumint": sa_types.MEDIUMINT(),
    "int": sa_types.INTEGER(),
    "integer": sa_types.INTEGER(),
    "bigint": sa_types.BIGINT(),
    "float": sa_types.FLOAT(),
    "real": sa_types.FLOAT(),
    "double": sa_types.DOUBLE(),
    "double precision": sa_types.DOUBLE(),
    "decimal": sa_types.DECIMAL(),
    "numeric": sa_types.DECIMAL(),
    "year": sa_types.YEAR(),
    "date": sa_types.DATE(),
    "datetime": sa_types.DATETIME(),
    "timestamp": sa_types.TIMESTAMP(),
    "time": sa_types.TIME(),
    "char": sa_types.CHAR(),
    "varchar": sa_types.VARCHAR(),
    "tinytext": sa_types.TINYTEXT(),
    "text": sa_types.TEXT(),
    "mediumtext": sa_types.MEDIUMTEXT(),
    "longtext": sa_types.LONGTEXT(),
    "enum": sa_types.ENUM(),
    "set": sa_types.SET(),
    "binary": sa_types.BINARY(),
    "varbinary": sa_types.VARBINARY(),
    "tinyblob": sa_types.TINYBLOB(),
    "blob": sa_types.BLOB(),
    "mediumblob": sa_types.MEDIUMBLOB(),
    "longblob": sa_types.LONGBLOB(),
    "json": sa_types.JSON(),
    "bit": sa_types.BIT(),
    "bool": sa_types.BOOLEAN(),
    "boolean": sa_types.BOOLEAN(),
}

_VERSION_PIECES = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")


class MySQLEngine(EngineSpelling):
    """MySQL 8 and the protocol-compatible forks that say so in ``version_comment``."""

    name = "mysql"
    dbapi = DEFAULT_DBAPI
    pip_extra = PIP_EXTRA
    default_port = 3306
    #: There is no schema layer: a MySQL *database* is the namespace, so the
    #: configured database is the schema and an empty default is the honest answer.
    default_schema = None
    #: ``REGEXP`` (and ``RLIKE``) exist on any string-ish column, since forever.
    supports_regex = True
    #: Table names are case-sensitive on the usual Linux deployment and the server's
    #: ``lower_case_table_names`` decides the rest, which the probe records rather
    #: than this constant guessing.
    name_case_rule = "sensitive"
    #: A name longer than 64 characters is refused by the server, so a mapping that
    #: carries one was not written against this database.
    max_identifier_length = 64

    # -- connection --------------------------------------------------------

    def url(self, config: ConnectConfig, *, password: Optional[str] = None) -> URL:
        """``mysql+pymysql``. TLS is expressed through ``connect_args``, not here.

        PyMySQL takes its TLS material as a dict, not as DSN keywords, so putting it
        in the URL query would hand the DBAPI a string where it wants a mapping.
        """
        driver = str(option_value(config, "dbapi", DEFAULT_DBAPI) or DEFAULT_DBAPI)
        query: Dict[str, object] = {"charset": str(option_value(config, "charset", CHARSET))}
        extra = option_value(config, URL_QUERY_OPTION, {})
        if isinstance(extra, Mapping):
            query.update({str(key): value for key, value in extra.items()})
        return URL.create(
            f"mysql+{driver}",
            username=config.username or "",
            password=password if password is not None else (config.password or None),
            host=str(config.host or "localhost"),
            port=int(config.port or self.default_port),
            # database_id is the database, which on MySQL is also the schema.
            database=str(config.database_id or ""),
            query={key: str(value) for key, value in query.items() if value not in (None, "")},
        )

    def connect_args(self, config: ConnectConfig) -> Dict[str, object]:
        """Charset, timeouts and the statement guard. No password.

        ``init_command`` is how PyMySQL runs one session statement at connect, and
        the timeout belongs there because it is milliseconds and per-server-version,
        i.e. something the config has an opinion about.
        """
        args: Dict[str, object] = {
            "charset": str(option_value(config, "charset", CHARSET)),
            "connect_timeout": timeout_seconds(
                config, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
            "read_timeout": timeout_seconds(
                config, QUERY_TIMEOUT_OPTION, DEFAULT_QUERY_TIMEOUT_SECONDS
            ),
            "init_command": (
                f"SET SESSION max_execution_time = {self.timeout_ms(config)}"
            ),
        }
        if config.use_tls:
            ssl: Dict[str, object] = {}
            for key, option in (
                ("ca", "ssl_ca"),
                ("cert", "ssl_cert"),
                ("key", "ssl_key"),
            ):
                stated = option_value(config, option, None)
                if isinstance(stated, str) and stated.strip():
                    ssl[key] = stated.strip()
            # What is sent, not what the client then does with it: a bare ``use_tls``
            # asks for an encrypted line and no more, and naming a CA is the point at
            # which verification is asked for. How a given PyMySQL version reads an
            # empty dict is its own, which is why ``options["connect_args"]`` merges
            # last and can state ``ssl_mode`` outright.
            if ssl.get("ca"):
                ssl["verify_cert"] = True
            args["ssl"] = ssl
        extra = option_value(config, CONNECT_ARGS_OPTION, {})
        if isinstance(extra, Mapping):
            args.update({str(key): value for key, value in extra.items()})
        return args

    def timeout_ms(self, config: ConnectConfig) -> int:
        """The statement guard in milliseconds, 0 meaning no guard.

        ``max_execution_time`` counts milliseconds, applies to **single SELECT
        statements**, and is not a transaction-wide limit — which is why the compiler
        gets the optimiser-hint form as well as this session default.
        """
        stated = option_value(config, "max_execution_time_ms", None)
        if stated is not None:
            try:
                return max(0, int(float(str(stated))))
            except (TypeError, ValueError):
                pass
        seconds = timeout_seconds(config, QUERY_TIMEOUT_OPTION, DEFAULT_QUERY_TIMEOUT_SECONDS)
        return seconds * 1000

    def timeout_hint(self, config: ConnectConfig) -> str:
        """``/*+ MAX_EXECUTION_TIME(n) */``, for the compiler to put after SELECT.

        The session variable covers the same ground but only for a *single* SELECT
        statement, and a plan that fans a selection out into several branches or
        wraps the read in a transaction is not one. The hint is per statement and the
        server ignores it (with no error) on anything that is not a top-level
        ``SELECT``, so an empty string here means the guard is the session variable.
        """
        ms = self.timeout_ms(config)
        return f"/*+ MAX_EXECUTION_TIME({ms}) */" if ms else ""

    def session_statements(self) -> Tuple[str, ...]:
        """Empty, on purpose: PyMySQL's ``charset`` and ``init_command`` connect
        args are these very statements, sent at connect by the DBAPI itself."""
        return ()

    def isolation(self, config: ConnectConfig) -> Optional[str]:
        """None by default — and that answer has a reason, unlike SQL Server's.

        InnoDB readers never block writers, so an analyst's long expand does not
        freeze someone editing records and needs no dirty reads to avoid it.
        ``READ UNCOMMITTED`` is genuinely *available* here, unlike on PostgreSQL, so a
        person may ask for it by name and get it; what they do not get is it chosen
        for them.
        """
        return pick_isolation(config, self.name, ISOLATION_LEVELS, None)

    def read_only_statement(self) -> Optional[str]:
        """``START TRANSACTION READ ONLY``: the per-transaction form, because MySQL has
        no session-level one.

        What it buys is a promise the server enforces for the whole transaction. The
        protection against a write from this proxy in the first place is the login's
        grants, and the setup page says so — a read-only login is the part of this a
        person has to do themselves.
        """
        return "START TRANSACTION READ ONLY"

    # -- types -------------------------------------------------------------

    def type_table(self) -> Mapping[str, str]:
        return _TYPE_TABLE

    def refine_type(self, declared: DeclaredType) -> Optional[str]:
        """``tinyint(1)`` is MySQL's flag column; ``tinyint(4)`` is a number.

        There is no boolean type, so ``TINYINT(1)`` — and its ``BOOL``/``BOOLEAN``
        spelling, which is literally an alias of it — is the only signal a column
        means true/false. The display width is the signal, which is why reading a
        ``tinyint(1)`` as an integer and showing it as ``1`` is the bug this catches.
        """
        if declared.base == "tinyint":
            return "BOOL" if declared.precision == 1 else "INT32"
        if declared.base == "bit":
            # ``BIT(1)`` is a flag; a wider ``BIT(n)`` is a number nobody can read off
            # the canvas as anything but an integer.
            return "BOOL" if declared.precision in (None, 1) else "INT64"
        return None

    def column_type(self, declared_type: Optional[str]) -> Optional[TypeEngine]:
        """Bind as the column's declared type, because MySQL converts otherwise.

        A numeric parameter against a ``VARCHAR`` key column makes MySQL cast *the
        column* for every row: the index is unusable and rows whose text is not a
        number compare as ``0``, so a lookup returns data the strict reading would
        refuse. A text parameter against a ``BIGINT`` column does the same thing in
        the other direction. Neither case raises, which is what makes this the
        engine's version of the bridge's pyodbc lesson: the fix is a typed parameter,
        never a cast written around the column.

        None means this map has no name for that type: the spatial types have none, and a
        text parameter against a ``POINT`` column is not a comparison MySQL defines.
        """
        declared = parse_declared_type(declared_type or "")
        if declared.array:
            # MySQL has no array column type, so an ``[]`` suffix here is a name the server
            # was never asked for — and reading it as its element is what made a filter
            # that cannot exist look like a predicate that merely missed. See ``array_binding``.
            return self.array_binding(declared)
        return _COLUMN_TYPES.get(declared.base)

    def unkeyable_types(self) -> frozenset:
        return UNKEYABLE_TYPES

    # -- casts -------------------------------------------------------------

    def agg_cast(self, function: str, column_type: Optional[str]) -> Optional[str]:
        """Always None: MySQL has already widened, in both directions.

        ``AVG`` returns ``DOUBLE`` for an integer column and ``DECIMAL`` for a decimal
        one, so there is no integer division to fix. ``SUM`` over a ``BIGINT`` widens
        to ``DECIMAL`` rather than overflowing, which is the exact failure the SQL
        Server cast exists to prevent — there is no equivalent failure here, and a
        cast copied over would only cost the index.
        """
        return None

    def group_key_cast(self, column_type: Optional[str]) -> Optional[str]:
        # ``GROUP BY`` on a TEXT column is legal here; the cost is memory, not an
        # error, and a cast would be the expensive part.
        return None

    def like_cast(self, column_type: Optional[str]) -> Optional[str]:
        """None. ``LIKE`` against a number compares after converting the column, and
        the fix for that is a typed parameter, not a cast around the column."""
        return None

    # -- server ------------------------------------------------------------

    def probe_sql(self) -> str:
        """``version_comment`` is what separates MySQL from MariaDB.

        ``VERSION()`` alone cannot: MariaDB reports ``10.6``/``11.4`` there, and the
        numbers do not line up with MySQL's, so a ``JSON_TABLE`` or an optimiser hint
        decided on ``major >= 8`` would be wrong in both directions.
        """
        return (
            "SELECT VERSION() AS version,"
            " @@version_comment AS comment,"
            " @@lower_case_table_names AS lower_case_table_names,"
            " @@read_only AS read_only"
        )

    def server_from_row(self, row: Sequence[object]) -> ServerInfo:
        version = str(row[0] or "")
        comment = str(row[1] or "")
        lower_case = _as_int(row[2])
        major, minor, build = _split_version(version)
        mariadb = "mariadb" in version.lower() or "mariadb" in comment.lower()
        named = f"{major}.{minor}.{build}" if build else f"{major}.{minor}"
        if mariadb:
            named = f"MariaDB {named}"
        return ServerInfo(
            engine=self.name,
            version=version,
            major=major,
            minor=minor,
            build=build,
            edition=comment,
            release_name=named,
            read_only_server=bool(_as_int(row[3])),
            mariadb=mariadb,
            names_case_insensitive=bool(lower_case),
        )

    def counts_statement(
        self, schema: Optional[str], names: Sequence[str]
    ) -> Tuple[str, Dict[str, object]]:
        """``information_schema.TABLES.TABLE_ROWS``: an estimate, from metadata only.

        Three caveats travel with it, and the caller has to know all three. It is
        ``NULL`` for engines that do not keep a count (a view, a MEMORY table on some
        builds) — those tables come back absent rather than as zero. For InnoDB it is
        the last persisted or computed estimate, so a table nobody has analysed since
        it was loaded reports a number with no relation to its contents. And
        ``lower_case_table_names`` decides whether the name in ``TABLES`` matches the
        name in the mapping by case, which is why the returned dict is keyed exactly
        as the server spelled it.
        """
        wanted = list(names)
        placeholders = ", ".join(f":n{index}" for index in range(len(wanted)))
        binds: Dict[str, object] = {f"n{index}": name for index, name in enumerate(wanted)}
        if schema:
            where = "TABLE_SCHEMA = :schema"
            binds["schema"] = schema
        else:
            where = "TABLE_SCHEMA = DATABASE()"
        if wanted:
            where += f" AND TABLE_NAME IN ({placeholders})"
        else:
            where += " AND 1 = 0"
        sql = (
            "SELECT TABLE_NAME AS table_name, TABLE_ROWS AS row_count"
            " FROM information_schema.TABLES"
            f" WHERE {where}"
        )
        return sql, binds

    # -- id lists ----------------------------------------------------------

    def id_list_strategy(self, info: ServerInfo, size: int) -> str:
        """``inline`` to 2 000, ``bulk`` through ``JSON_TABLE`` on 8.0.4+, otherwise
        a ``fanout`` of placeholder chunks.

        MySQL has no array parameter to bind a list into, so a big selection is
        either shredded from one JSON document or chopped up. Below 8.0.4 there is no
        ``JSON_TABLE`` to shred with, and a MariaDB server's version number is not
        MySQL's, so both take the chunked route — which pages across branches only
        best-effort and says so in the result's summary.
        """
        if size <= MYSQL_INLINE_ID_MAX:
            return "inline"
        return "bulk" if info.supports_json_table else "fanout"

    def bulk_id_form(self, info: ServerInfo) -> str:
        return "json_table" if info.supports_json_table else "placeholders"

    def json_document_type(self) -> str:
        # ``JSON_TABLE`` takes a JSON value, and naming the type is what stops PyMySQL
        # sending the document as a string the server then has to resolve on its own.
        return "json"

    def shredded_column_type(self, declared_type: Optional[str]) -> str:
        # Whole keys shred as ``BIGINT``; text as ``VARCHAR(450)`` — 1 800 bytes under
        # utf8mb4, inside the InnoDB index-key limit, so the shredded column compares
        # against the key without MySQL converting the column, which is the failure
        # this engine's whole module is written around.
        return "BIGINT" if ids.is_integral_type(declared_type) else "VARCHAR(450)"

    def fanout_chunk_size(self, info: ServerInfo) -> int:
        return MYSQL_FANOUT_CHUNK


def _split_version(version: str) -> Tuple[int, int, int]:
    match = _VERSION_PIECES.match(str(version or ""))
    if not match:
        return (0, 0, 0)
    parts = [int(piece) if piece else 0 for piece in match.groups()]
    return (parts[0], parts[1], parts[2])


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
