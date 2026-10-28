"""
The proxy's hard caps, pinned to the numbers the docs promise.

Each test imports one constant and asserts the value matches the figure the
documentation states at the line its docstring names. If a future edit raises
or lowers a cap, the matching pin fails here, not at the route that depends
on it.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.relational.catalog import (
    MAX_EDGES,
    MAX_LABELS,
    MAX_PROPS,
)
from kineviz_database_proxy.drivers.relational.mapping_io import MAX_MAPPING_BYTES
from kineviz_database_proxy.drivers.relational.reads import PREVIEW_ROWS_MAX
from kineviz_database_proxy.services.graph_schema_cache import (
    DEFAULT_TTL_SECONDS,
    MAX_ENTRIES,
)


def test_max_labels_is_200_as_doc_relational_mapping_md_81_says() -> None:
    """``doc/RELATIONAL_MAPPING.md:81-82`` claims 200 labels per record."""
    assert MAX_LABELS == 200, MAX_LABELS


def test_max_edges_is_400_as_doc_relational_mapping_md_81_says() -> None:
    """``doc/RELATIONAL_MAPPING.md:81-82`` claims 400 edges per record."""
    assert MAX_EDGES == 400, MAX_EDGES


def test_max_props_is_200_as_doc_relational_mapping_md_81_says() -> None:
    """``doc/RELATIONAL_MAPPING.md:81-82`` claims 200 properties per record."""
    assert MAX_PROPS == 200, MAX_PROPS


def test_max_mapping_bytes_is_1_mib_as_doc_relational_mapping_md_251_says() -> None:
    """``doc/RELATIONAL_MAPPING.md:251`` claims a 1 MiB mapping-file cap."""
    assert MAX_MAPPING_BYTES == 1024 * 1024, MAX_MAPPING_BYTES


def test_preview_rows_max_is_100_as_doc_relational_api_md_297_says() -> None:
    """``doc/RELATIONAL_API.md:297`` claims the preview row cap is 100."""
    assert PREVIEW_ROWS_MAX == 100, PREVIEW_ROWS_MAX


def test_default_ttl_seconds_is_60s_as_doc_api_reference_md_209_says() -> None:
    """``doc/API_Reference.md:209`` says 60s by default; pin to that exact value."""
    assert DEFAULT_TTL_SECONDS == 60.0, DEFAULT_TTL_SECONDS


def test_max_entries_is_an_int_in_the_doc_range() -> None:
    """``doc/API_Reference.md`` does not name a number; the type and range pin it."""
    assert isinstance(MAX_ENTRIES, int), type(MAX_ENTRIES)
    assert 1 < MAX_ENTRIES <= 1024, MAX_ENTRIES
