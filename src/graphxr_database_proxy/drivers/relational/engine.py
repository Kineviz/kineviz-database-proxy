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

# Re-exported so every importer of ``relational.engine`` keeps working while the
# body sits in the ``engine_*`` modules beside it, in dependency order: terms and
# types import nothing local, server and config read the terms, EngineSpelling
# reads all four, and the registry is what ``engines/`` registers itself into.
from .engine_terms import (
    BULK_ID_FORMS,
    BulkIdForm,
    CONNECT_ARGS_OPTION,
    CONNECT_TIMEOUT_OPTION,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    ID_LIST_STRATEGIES,
    ISOLATION_OPTION,
    IdListStrategy,
    MSSQL_FANOUT_CHUNK,
    MSSQL_INLINE_ID_MAX,
    MSSQL_MAX_PARAMS,
    MSSQL_MIN_SUPPORTED_MAJOR,
    MSSQL_OPENJSON_MIN_COMPAT,
    MSSQL_OPENJSON_MIN_MAJOR,
    MYSQL_FANOUT_CHUNK,
    MYSQL_INLINE_ID_MAX,
    MYSQL_JSON_TABLE_MIN,
    MYSQL_MIN_SUPPORTED,
    NUMERIC_PROPERTY_TYPES,
    NameCaseRule,
    POSTGRESQL_INLINE_ID_MAX,
    POSTGRESQL_MIN_SUPPORTED,
    POSTGRESQL_REFUSED_ISOLATION,
    PROPERTY_TYPES,
    PropertyType,
    QUERY_TIMEOUT_OPTION,
    SCHEMA_OPTION,
    URL_QUERY_OPTION,
    _CONTROL_CHARS,
    _IDENTIFIER_SAFE,
)
from .engine_types import (
    DeclaredType,
    _CAST_CHARACTER_BASES,
    _CAST_DECIMAL,
    _first_int,
    cast_sqlalchemy_type,
    parse_declared_type,
)
from .engine_server import (
    ProbedConnection,
    ServerInfo,
)
from .engine_config import (
    ConnectConfig,
    _option,
    configured_schema,
    option_value,
    pick_isolation,
    timeout_seconds,
)
from .engine_spelling import (
    EngineSpelling,
)
from .engine_registry import (
    UnknownEngine,
    _LOADED,
    _REGISTRY,
    _ensure_loaded,
    engines_for,
    register,
    registered_engines,
)
