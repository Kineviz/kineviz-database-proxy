"""
A link's resolved endpoint pairs: the columns they occupy, and the list left after a
repeated pair is folded into the one it duplicates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Sequence, Set, Tuple

if TYPE_CHECKING:
    from .schemas import Pair


def _columns_of(pairs: Sequence[Pair]) -> Set[str]:
    out: Set[str] = set()
    for pair in pairs:
        out.add(pair.src.column)
        out.add(pair.dst.column)
    return out


def _deduped(pairs: Sequence[Pair]) -> List[Pair]:
    """The same two ends named twice is one pair, and the bigger count is the guess to keep."""
    seen: Dict[Tuple[str, str, str, str], Pair] = {}
    for pair in pairs:
        mark = (pair.src.label, pair.src.column, pair.dst.label, pair.dst.column)
        keep = seen.get(mark)
        if keep is None or pair.row_estimate > keep.row_estimate:
            seen[mark] = pair
    return list(seen.values())
