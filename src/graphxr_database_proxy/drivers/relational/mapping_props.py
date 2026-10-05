"""
The columns a record exposes, worked out once for either kind of record and either
source of the list, so the document walk and the catalog maps stay free of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Set, Union

from ...models.graph_mapping import MappingEdge, MappingNode

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
    """
    excluded = {str(name) for name in record.exclude_properties}
    source = dict(record.properties)
    if not source:
        source = dict(index.columns.get(table, {}))
    else:
        for name in list(source):
            actual = index.column_type(table, name)
            if actual is not None:
                source[name] = actual
            elif index.live and index.columns.get(table) and name not in drop and name not in excluded:
                out.warnings.append(
                    "{}.properties: column {!r} is not a column of {!r} in this "
                    "schema".format(path, name, table)
                )
    return {
        name: type_name
        for name, type_name in source.items()
        if name not in excluded and name not in drop
    }
