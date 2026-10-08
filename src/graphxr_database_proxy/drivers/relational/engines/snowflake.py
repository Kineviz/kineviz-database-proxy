"""Snowflake connection policy and SQL types for the shared relational pipeline."""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import URL, types

from ..engine import (
    ConnectConfig, DeclaredType, EngineSpelling, ServerInfo, configured_schema,
    option_value, parse_declared_type, pick_isolation, timeout_seconds,
)

_TYPES = {
    **dict.fromkeys(("number", "numeric", "decimal", "dec", "fixed"), "DECIMAL"),
    **dict.fromkeys(("int", "integer", "bigint", "smallint", "tinyint", "byteint"), "INT64"),
    **dict.fromkeys(("float", "float4", "float8", "real", "double", "double precision"), "DOUBLE"),
    **dict.fromkeys(("varchar", "char", "character", "string", "text"), "STRING"),
    **dict.fromkeys(("binary", "varbinary"), "BYTES"),
    "boolean": "BOOL", "date": "DATE", "time": "STRING",
    **dict.fromkeys(("timestamp", "timestamp_ntz", "timestamp_ltz", "timestamp_tz", "datetime"), "TIMESTAMP"),
    **dict.fromkeys(("variant", "object", "array"), "JSON"),
}


class SnowflakeEngine(EngineSpelling):
    name = "snowflake"
    dbapi = "snowflake.sqlalchemy"
    pip_extra = "snowflake-sqlalchemy"
    default_port = 443
    default_schema = "PUBLIC"
    # Physical names are preserved by our catalog and quoted by the compiler.
    name_case_rule = "sensitive"
    max_identifier_length = 255
    supports_catalog_paging = True
    identifier_quote = True
    discovery_warnings = (
        "Snowflake standard tables do not enforce PRIMARY KEY, UNIQUE or FOREIGN KEY constraints. "
        "Verify that each mapped key is unique and non-null and that relationship columns refer to valid rows.",
    )

    def url(self, config: ConnectConfig, *, password: Optional[str] = None) -> URL:
        account = str(option_value(config, "account", config.host or "")).strip()
        if "://" in account or "/" in account or "@" in account:
            raise ValueError("Snowflake account must be an account identifier, without a URL scheme or path")
        suffix = ".snowflakecomputing.com"
        if account.lower().endswith(suffix):
            account = account[:-len(suffix)]
        if not account:
            raise ValueError("Enter the Snowflake account identifier, for example myorg-myaccount")
        # Put namespace names in connector arguments rather than a slash-delimited
        # URL path, preserving quoted names and keeping the password masked by URL.
        return URL.create(
            "snowflake", host=account, username=config.username or "",
            password=password if password is not None else config.password or None,
        )

    def connect_args(self, config: ConnectConfig) -> Dict[str, object]:
        args: Dict[str, object] = {
            "database": str(config.database_id or ""),
            "schema": configured_schema(config, self.default_schema),
            "login_timeout": timeout_seconds(config, "connect_timeout", 15),
            "network_timeout": timeout_seconds(config, "query_timeout", 120),
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": timeout_seconds(config, "query_timeout", 120),
                "QUOTED_IDENTIFIERS_IGNORE_CASE": False,
                "QUERY_TAG": "graphxr-database-proxy",
            },
        }
        for name in ("warehouse", "role", "authenticator"):
            value = option_value(config, name, None)
            if value:
                args[name] = str(value)
        return args

    def session_statements(self) -> Tuple[str, ...]:
        # SELECT-only statements are enforced by the shared translator. Snowflake
        # has no READ ONLY transaction mode; database privileges belong to the role.
        return ()

    def isolation(self, config: ConnectConfig) -> Optional[str]:
        return pick_isolation(config, self.name, ("READ COMMITTED", "AUTOCOMMIT"), None)

    def type_table(self) -> Mapping[str, str]:
        return _TYPES

    def refine_type(self, declared: DeclaredType) -> Optional[str]:
        if declared.base in {"number", "numeric", "decimal", "dec", "fixed"}:
            return "INT64" if declared.scale in (None, 0) else "DECIMAL"
        return None

    def column_type(self, declared_type: Optional[str]) -> Optional[types.TypeEngine]:
        declared = parse_declared_type(declared_type or "")
        if declared.array:
            return None
        base = declared.base
        kind = _TYPES.get(base)
        if base in {"number", "numeric", "decimal", "dec", "fixed"}:
            return types.DECIMAL(declared.precision or 38, declared.scale or 0)
        if kind == "INT64":
            return types.BigInteger()
        if kind == "DOUBLE":
            return types.Float()
        if kind == "STRING":
            return types.Time() if base == "time" else types.String(declared.precision)
        if kind == "BYTES":
            return types.LargeBinary()
        if kind == "BOOL":
            return types.Boolean()
        if kind == "DATE":
            return types.Date()
        if kind == "TIMESTAMP":
            return types.DateTime(timezone=base in {"timestamp_tz", "timestamp_ltz"})
        # Semi-structured columns remain readable; scalar comparisons against them
        # need explicit path semantics and must not silently bind as text.
        return None

    def unkeyable_types(self) -> frozenset:
        return frozenset({"variant", "object", "array", "geography", "geometry", "vector"})

    def agg_cast(self, function: str, column_type: Optional[str]) -> Optional[str]:
        return None

    def probe_sql(self) -> str:
        return "SELECT CURRENT_VERSION(), CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_WAREHOUSE(), CURRENT_ROLE()"

    def server_from_row(self, row: Sequence[object]) -> ServerInfo:
        version = str(row[0] or "")
        parts = [int(part) for part in re.findall(r"\d+", version)[:3]]
        major, minor, build = (parts + [0, 0, 0])[:3]
        return ServerInfo(engine=self.name, version=version, major=major, minor=minor, build=build,
                          edition="Snowflake", release_name=version, names_case_insensitive=False)

    def counts_statement(self, schema: Optional[str], names: Sequence[str]) -> Tuple[str, Dict[str, object]]:
        binds: Dict[str, object] = {"schema": schema or self.default_schema}
        placeholders = []
        for index, name in enumerate(names):
            binds[f"name{index}"] = name
            placeholders.append(f":name{index}")
        return (
            "SELECT TABLE_NAME, ROW_COUNT FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = :schema AND TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME IN ("
            + (", ".join(placeholders) or "NULL") + ")", binds,
        )

    def id_list_strategy(self, info: ServerInfo, size: int) -> str:
        return "inline" if size <= 1000 else "fanout"

    def bulk_id_form(self, info: ServerInfo) -> str:
        return "placeholders"

    def json_document_type(self) -> str:
        return "varchar"

    def shredded_column_type(self, declared_type: Optional[str]) -> str:
        return "varchar"

    def fanout_chunk_size(self, info: ServerInfo) -> int:
        return 1000

    def catalog_inspector(self, engine: object, config: ConnectConfig) -> object:
        from ..snowflake_catalog import SnowflakeInspector
        return SnowflakeInspector(engine, str(config.database_id or ""))
