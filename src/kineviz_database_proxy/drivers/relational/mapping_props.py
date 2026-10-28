"""
The columns a record exposes, worked out once for either kind of record and either
source of the list, so the document walk and the catalog maps stay free of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Set, Union

from ...models.graph_mapping import MappingEdge, MappingNode
from .mapping_index import names_a_type

if TYPE_CHECKING:
    from .mapping import EffectiveMapping
    from .mapping_index import _CatalogIndex

_Record = Union[MappingNode, MappingEdge]


def _props(
    record: _Record,
    table: str,
    index: _CatalogIndex,
    drop: Set[str],
    path: str,
    out: EffectiveMapping,
) -> Dict[str, str]:
    """
    The columns this record exposes, typed the way the live schema says they are.

    A stated list is a whitelist and stays the authority — that is exactly what makes
    an unmapped column invisible, which is why the editor counts them. Empty means
    take discovery's columns. Either way a known table corrects a type, because
    binding a parameter as the wrong type costs a full scan and raises no error.

    A correction needs something to correct *with*. Where the catalog has no name for the
    column's type at all — :data:`mapping_index.NO_TYPE` — the declaration is the only type
    information there is, so it survives: that is the one place the document is the escape
    hatch a refused bind tells a person to use.
    """
    excluded = {str(name) for name in record.exclude_properties}
    source = dict(record.properties)
    if not source:
        source = dict(index.columns.get(table, {}))
    else:
        for name in list(source):
            stated = source[name]
            column = record.property_columns.get(name, name)
            actual = index.column_type(table, column)
            if actual is None:
                if index.live and index.columns.get(table) and name not in drop and name not in excluded:
                    out.warnings.append(
                        "{}.properties: column {!r} is not a column of {!r} in this "
                        "schema".format(path, column, table)
                    )
            elif not names_a_type(actual) and names_a_type(stated):
                out.warnings.append(
                    "{}.properties: this schema names no type for column {!r}, so the "
                    "declared {!r} is what a filter on it binds as".format(path, name, stated)
                )
            else:
                source[name] = actual
    return {
        name: type_name
        for name, type_name in source.items()
        if name not in excluded and name not in drop
    }
