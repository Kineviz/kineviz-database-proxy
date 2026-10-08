"""Bounded Snowflake catalog reads preserving physical identifier spelling.

The SQLAlchemy dialect normalizes unquoted uppercase names to lowercase. The
mapping compiler quotes every physical name, so its catalog must retain the
server's spelling instead. No schema-wide column cache is populated here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from .catalog import INTERNAL_SCHEMAS, SYSTEM_TABLE_NAMES, SYSTEM_TABLE_PREFIXES


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SnowflakeInspector:
    def __init__(self, engine: Any, database: str) -> None:
        self.engine = engine
        self.database = database

    def _rows(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            return [
                {str(key).lower(): value for key, value in row.items()}
                for row in connection.execute(text(sql), params or {}).mappings()
            ]

    def _names_where(self, schema: Optional[str], search: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        clauses = ["TABLE_SCHEMA = :schema", "TABLE_TYPE = 'BASE TABLE'"]
        params: Dict[str, Any] = {"schema": schema or "PUBLIC"}
        for index, prefix in enumerate(SYSTEM_TABLE_PREFIXES):
            clauses.append(f"LEFT(LOWER(TABLE_NAME), {len(prefix)}) <> :prefix{index}")
            params[f"prefix{index}"] = prefix
        for index, name in enumerate(SYSTEM_TABLE_NAMES):
            clauses.append(f"LOWER(TABLE_NAME) <> :system{index}")
            params[f"system{index}"] = name
        if search:
            clauses.append("CONTAINS(LOWER(TABLE_NAME), LOWER(:search))")
            params["search"] = search
        return " AND ".join(clauses), params

    def get_table_names_page(self, schema: Optional[str], offset: int, limit: int,
                             search: Optional[str] = None) -> Tuple[List[str], int]:
        if (schema or "PUBLIC").lower() in INTERNAL_SCHEMAS:
            return [], 0
        where, params = self._names_where(schema, search)
        total = self._rows("SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.TABLES WHERE " + where, params)[0]["total"]
        rows = self._rows("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE " + where
                          + " ORDER BY TABLE_NAME LIMIT :limit OFFSET :offset",
                          {**params, "limit": limit, "offset": offset})
        return [row["table_name"] for row in rows], int(total)

    def get_table_names(self, schema: Optional[str] = None) -> List[str]:
        # Full discovery consumes every name, while the picker uses the source-
        # paged method above. Even discovery reads names in bounded batches.
        names: List[str] = []
        offset = 0
        while True:
            batch, total = self.get_table_names_page(schema, offset, 500)
            names.extend(batch)
            offset += len(batch)
            if not batch or offset >= total:
                return names

    def get_view_names(self, schema: Optional[str] = None) -> List[str]:
        # Both the name and column reads already restrict to base tables.
        return []

    def get_multi_columns(self, filter_names: Optional[Sequence[str]] = None,
                          schema: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        names = list(filter_names) if filter_names is not None else self.get_table_names(schema)
        if not names:
            return {}
        params: Dict[str, Any] = {"schema": schema or "PUBLIC"}
        for index, name in enumerate(names):
            params[f"table{index}"] = name
        slots = ", ".join(f":table{index}" for index in range(len(names)))
        rows = self._rows(
            "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, "
            "c.CHARACTER_MAXIMUM_LENGTH, c.IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS c "
            "JOIN INFORMATION_SCHEMA.TABLES t ON c.TABLE_CATALOG = t.TABLE_CATALOG "
            "AND c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME "
            "WHERE c.TABLE_SCHEMA = :schema AND t.TABLE_TYPE = 'BASE TABLE' "
            f"AND c.TABLE_NAME IN ({slots}) ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION", params,
        )
        result: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            declared = row["data_type"]
            if declared in {"NUMBER", "DECIMAL", "NUMERIC"}:
                declared = f"DECIMAL({row['numeric_precision'] or 38},{row['numeric_scale'] or 0})"
            elif declared in {"TEXT", "VARCHAR"} and row["character_maximum_length"]:
                declared = f"VARCHAR({row['character_maximum_length']})"
            result.setdefault(row["table_name"], []).append({
                "name": row["column_name"], "type": declared,
                "nullable": row["is_nullable"] == "YES",
            })
        return result

    def _keys(self, command: str, table: str, schema: Optional[str]) -> List[Dict[str, Any]]:
        qualified = ".".join(_quote(name) for name in (schema or "PUBLIC", table))
        return self._rows(f"SHOW {command} KEYS IN TABLE {qualified}")

    def get_multi_pk_constraint(self, filter_names: Optional[Sequence[str]] = None,
                                schema: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        result = {}
        for table in filter_names or []:
            rows = sorted(self._keys("PRIMARY", table, schema), key=lambda row: row["key_sequence"])
            result[table] = {"name": rows[0]["constraint_name"] if rows else None,
                             "constrained_columns": [row["column_name"] for row in rows]}
        return result

    def get_multi_unique_constraints(self, filter_names: Optional[Sequence[str]] = None,
                                     schema: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        result = {}
        for table in filter_names or []:
            constraints: Dict[str, Dict[str, Any]] = {}
            for row in sorted(self._keys("UNIQUE", table, schema), key=lambda row: row["key_sequence"]):
                entry = constraints.setdefault(row["constraint_name"], {"name": row["constraint_name"], "constrained_columns": []})
                entry["constrained_columns"].append(row["column_name"])
            result[table] = list(constraints.values())
        return result

    def get_multi_foreign_keys(self, filter_names: Optional[Sequence[str]] = None,
                               schema: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        result = {}
        for table in filter_names or []:
            constraints: Dict[str, Dict[str, Any]] = {}
            for row in sorted(self._keys("IMPORTED", table, schema), key=lambda row: row["key_sequence"]):
                target_schema = row["pk_schema_name"]
                target_database = row.get("pk_database_name")
                if target_database and target_database != row.get("fk_database_name", self.database):
                    target_schema = f"{target_database}.{target_schema}"
                entry = constraints.setdefault(row["fk_name"], {
                    "name": row["fk_name"], "constrained_columns": [],
                    "referred_schema": target_schema, "referred_table": row["pk_table_name"], "referred_columns": [],
                })
                entry["constrained_columns"].append(row["fk_column_name"])
                entry["referred_columns"].append(row["pk_column_name"])
            result[table] = list(constraints.values())
        return result

    def get_multi_indexes(self, filter_names: Optional[Sequence[str]] = None,
                          schema: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        # SHOW UNIQUE KEYS supplies the identity candidates for standard and hybrid tables.
        return {}
