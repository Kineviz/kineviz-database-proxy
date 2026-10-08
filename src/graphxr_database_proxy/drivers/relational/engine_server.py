"""
What one probe round trip learned about a server, and the connection it learns it through.

Split out of ``engine.py``. The fields of :class:`ServerInfo` are facts and
its ``supports_*`` properties are the same facts read as policy, so no caller
re-derives a version comparison at the call site; :class:`ProbedConnection` is
the one call the probe and the statistics reader make."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Protocol, Sequence, Tuple

from .engine_terms import (
    MSSQL_MIN_SUPPORTED_MAJOR,
    MSSQL_OPENJSON_MIN_COMPAT,
    MSSQL_OPENJSON_MIN_MAJOR,
    MYSQL_JSON_TABLE_MIN,
    MYSQL_MIN_SUPPORTED,
    POSTGRESQL_MIN_SUPPORTED,
)


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
        if self.engine == "snowflake":
            return True
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


class ProbedConnection(Protocol):
    """The one call the version probe and the statistics reader make.

    ``sqlalchemy.Connection`` satisfies it; a test double returns rows as sequences
    and records the statement, which is why this is a protocol and not an engine
    import.
    """

    def execute(
        self, statement: object, parameters: Optional[Mapping[str, object]] = None
    ) -> Iterable[Sequence[object]]: ...
