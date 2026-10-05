"""
The vocabularies this seam speaks, and the numbers that are engine truth.

Split out of ``engine.py``, which re-exports it. A number here is a fact
about a server -- a parameter ceiling, the first release with a feature --
rather than a knob, which is why it sits beside the closed sets instead of
in a config file."""

from __future__ import annotations

import re
from typing import Tuple

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
