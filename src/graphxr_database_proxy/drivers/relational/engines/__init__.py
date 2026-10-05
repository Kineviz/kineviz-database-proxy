"""The engine spellings this proxy can serve, and the registry that hands them out.

Importing this package is what registers the engines; ``engine.engines_for()`` does
it on first lookup so no caller has to know. A new relational backend is one module
here plus one line below — and, per ``CLAUDE.md``, one case in each of the client's
two proxy tables in graphxr-dev, or it silently gets the bolt family's dialect.
"""

from __future__ import annotations

from ..engine import EngineSpelling, register
from .mssql import MSSQLEngine
from .mysql import MySQLEngine
from .postgresql import PostgreSQLEngine

#: One instance per engine: the spellings are stateless, and every answer that needs
#: a server takes that server's ``ServerInfo`` as an argument instead of caching one.
MSSQL = MSSQLEngine()
POSTGRESQL = PostgreSQLEngine()
MYSQL = MySQLEngine()

for _spelling in (MSSQL, POSTGRESQL, MYSQL):
    register(_spelling)
del _spelling

#: Every registered spelling, in the order the UI lists them.
ENGINES = (MSSQL, POSTGRESQL, MYSQL)

__all__ = [
    "ENGINES",
    "EngineSpelling",
    "MSSQL",
    "MYSQL",
    "POSTGRESQL",
    "register",
]
