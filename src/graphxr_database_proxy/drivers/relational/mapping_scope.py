"""
The document's scope: the ``include`` / ``exclude`` lists and what they decide.

A name is matched casefolded and may be a label or a table, so ``Employment`` reaches
the link that lives on that table even when nobody typed the word as a label. The
names that hit are remembered so a scope entry naming nothing can be said once — and
only with a schema to check it against, which is the catalog index's call, not this
module's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence, Set

if TYPE_CHECKING:
    from ...models.graph_mapping import GraphMapping


class _Scope:
    """The ``include`` / ``exclude`` lists, matched against a record's label or its table."""

    def __init__(self, whitelist: Sequence[str], blacklist: Sequence[str]) -> None:
        self.whitelist = list(whitelist)
        self.blacklist = list(blacklist)
        self.matched: Set[str] = set()

    @classmethod
    def of(cls, doc: Optional[GraphMapping]) -> "_Scope":
        if doc is None:
            return cls([], [])
        return cls(list(doc.include), list(doc.exclude))

    def out(self, names: Sequence[str]) -> bool:
        """True when this record is not in the graph, marking the names that decided it."""
        keys = {str(name).casefold() for name in names if name}
        if self.whitelist and not _any_hit(self.whitelist, keys, self.matched):
            return True
        return _any_hit(self.blacklist, keys, self.matched)


def _any_hit(names: Sequence[str], keys: Set[str], matched: Set[str]) -> bool:
    found = False
    for name in names:
        if name.casefold() in keys:
            matched.add(name)
            found = True
    return found
