"""Adapt GraphXR's version 2 mapping to the editor's canonical document shape."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError


def _invalid(path: tuple, message: str, value: Any) -> None:
    raise ValidationError.from_exception_data(
        "GraphMapping",
        [
            {
                "type": "value_error",
                "loc": path,
                "input": value,
                "ctx": {"error": ValueError(message)},
            }
        ],
    )


def canonical_mapping(value: Any) -> Any:
    """Keep v1 intact; translate v2 source, relationships and property columns.

    Canonical v2 documents (returned to the editor and persisted) also take this
    path, without being translated twice. Unknown fields remain for the strict
    document models to reject rather than disappearing during conversion.
    """
    if not isinstance(value, dict) or value.get("version") != 2:
        return value
    raw = deepcopy(value)
    if "source" in raw:
        source = raw.pop("source")
        if not isinstance(source, dict):
            _invalid(("source",), "source must be an object", source)
        for key in source:
            if key != "schema":
                _invalid(("source", key), "not a field of mapping source", source[key])
        if "schema" in source:
            if "schema" in raw or "schema_name" in raw:
                _invalid(
                    ("source", "schema"),
                    "specify the schema only once",
                    source["schema"],
                )
            raw["schema"] = source["schema"]
    if "relationships" in raw:
        if "edges" in raw:
            _invalid(
                ("relationships",),
                "specify relationships or edges, not both",
                raw["relationships"],
            )
        raw["edges"] = raw.pop("relationships")
    for section in ("nodes", "edges"):
        records = raw.get(section, [])
        if not isinstance(records, list):
            continue  # The document model names the structural error.
        public_section = (
            "relationships"
            if section == "edges" and "relationships" in value
            else section
        )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            path = (public_section, index)
            if section == "edges" and ("from" in record or "to" in record):
                if "endpoints" in record:
                    _invalid(
                        path + ("endpoints",),
                        "specify from/to or endpoints, not both",
                        record["endpoints"],
                    )
                for end in ("from", "to"):
                    if end not in record:
                        _invalid(path + (end,), "both from and to are required", record)
                record["endpoints"] = [
                    {"src": record.pop("from"), "dst": record.pop("to")}
                ]
            properties = record.get("properties", {})
            if not isinstance(properties, dict):
                continue
            columns = record.get("property_columns", {})
            if not isinstance(columns, dict):
                _invalid(
                    path + ("property_columns",),
                    "property_columns must be an object",
                    columns,
                )
            columns = dict(columns)
            for name, prop in properties.items():
                if not isinstance(prop, dict):
                    continue  # Canonical property types and v1 shorthand remain valid.
                for key in prop:
                    if key not in {"column", "type"}:
                        _invalid(
                            path + ("properties", name, key),
                            "not a field of a property",
                            prop[key],
                        )
                column = prop.get("column")
                declared = prop.get("type")
                for key, item in (("column", column), ("type", declared)):
                    if not isinstance(item, str) or not item.strip():
                        _invalid(
                            path + ("properties", name, key),
                            "a non-empty string is required",
                            item,
                        )
                if column != name:
                    columns[name] = column
                properties[name] = declared
            key_column = record.get("key")
            reserved = {key_column} if isinstance(key_column, str) else set()
            pairs = record.get("endpoints", [])
            for pair in pairs if isinstance(pairs, list) else []:
                if isinstance(pair, dict):
                    for end in ("src", "dst"):
                        endpoint = pair.get(end)
                        if isinstance(endpoint, dict):
                            endpoint_column = endpoint.get("column")
                            if isinstance(endpoint_column, str):
                                reserved.add(endpoint_column)
            for name, column in columns.items():
                if name in reserved and name != column:
                    _invalid(
                        path + ("properties", name, "column"),
                        "a property alias cannot replace an identity or endpoint column",
                        column,
                    )
            if columns:
                record["property_columns"] = columns
    return raw


def external_mapping(data: dict) -> dict:
    """Export a canonical document in GraphXR v2 form, retaining editor metadata."""
    raw = deepcopy(data)
    raw["version"] = 2
    schema = raw.pop("schema", None)
    raw["source"] = {"schema": schema} if schema is not None else {}
    raw["relationships"] = raw.pop("edges", [])
    for section in ("nodes", "relationships"):
        for record in raw.get(section, []):
            columns = record.pop("property_columns", {})
            record["properties"] = {
                name: {"column": columns.get(name, name), "type": declared}
                for name, declared in record.get("properties", {}).items()
            }
            if section == "relationships":
                pairs = record.get("endpoints", [])
                if len(pairs) == 1 and "row_estimate" not in pairs[0]:
                    pair = record.pop("endpoints")[0]
                    record["from"] = pair["src"]
                    record["to"] = pair["dst"]
    # Put the public format's principal fields first, consistently with its example.
    return {
        key: raw.pop(key) for key in ("version", "source", "nodes", "relationships")
    } | raw
