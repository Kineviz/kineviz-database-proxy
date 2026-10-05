"""
A link's resolved endpoint pairs: the columns they occupy, and the list left after a
repeated pair is folded into the one it duplicates.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Set, Tuple

from .schemas import Pair, size_rank


def _columns_of(pairs: Sequence[Pair]) -> Set[str]:
    out: Set[str] = set()
    for pair in pairs:
        out.add(pair.src.column)
        out.add(pair.dst.column)
    return out


def _deduped(pairs: Sequence[Pair]) -> List[Pair]:
    """
    The same two ends named twice is one pair, and the count worth keeping is the one that
    was taken.

    Ranked by ``size_rank``, the same rule ``representative`` uses, because a duplicate fold
    picks the survivor that later decides which pair a relationship is drawn between: an
    unmeasured duplicate must not replace a measured one, or the fold would trade a number
    for a gap and the surviving pair would report itself as counted.
    """
    seen: Dict[Tuple[str, str, str, str], Pair] = {}
    for pair in pairs:
        mark = (pair.src.label, pair.src.column, pair.dst.label, pair.dst.column)
        keep = seen.get(mark)
        if keep is None or size_rank(pair) > size_rank(keep):
            seen[mark] = pair
    return list(seen.values())
