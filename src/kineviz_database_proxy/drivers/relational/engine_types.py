"""
The declared-type grammar: a column type taken apart, and a cast name read back.

Split out of ``engine.py``. Both halves are here because an engine answers a
type the way the server spells it and the rest of the seam needs a decision:
:func:`parse_declared_type` is the only place that knows where the arguments
end, and :func:`cast_sqlalchemy_type` the only one that knows which spellings
a cast may ask for."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import DECIMAL
from sqlalchemy.types import NVARCHAR, TypeEngine


@dataclass(frozen=True)
class DeclaredType:
    """A column type as the server spelled it, taken apart."""

    base: str
    precision: Optional[int] = None
    scale: Optional[int] = None
    unsigned: bool = False
    array: int = 0


def parse_declared_type(
    db_type: str, *, precision: Optional[int] = None, scale: Optional[int] = None
) -> DeclaredType:
    """``nvarchar(200)``, ``numeric(10,2) unsigned``, ``text[]`` -> base + args.

    Only the parts a vocabulary decision needs are kept. Nothing is discarded: an
    unrecognised type is still a column, and it becomes ``STRING`` rather than
    absence.
    """
    raw = re.sub(r"\s+", " ", str(db_type or "").strip().lower())
    array = 0
    while raw.endswith("[]"):
        array += 1
        raw = raw[:-2].rstrip()
    args: Sequence[str] = ()
    match = re.search(r"\(([^()]*)\)\s*$", raw)
    if match:
        args = tuple(piece.strip() for piece in match.group(1).split(","))
        raw = raw[: match.start()].rstrip()
    unsigned = "unsigned" in raw
    # Only the modifiers that are never part of a type name go; ``character
    # varying`` and ``double precision`` stay whole and are looked up as written,
    # because collapsing them onto a stem is how a lookup starts inventing names.
    raw = re.sub(r"\b(unsigned|signed|zerofill)\b", " ", raw)
    raw = re.sub(r"\b(without time zone|with time zone|local time zone)\b", " ", raw)
    base = re.sub(r"\s+", " ", raw).strip()
    return DeclaredType(
        base=base,
        precision=_first_int(precision, args, 0),
        scale=_first_int(scale, args, 1),
        unsigned=unsigned,
        array=array,
    )


def _first_int(explicit: Optional[int], args: Sequence[str], index: int) -> Optional[int]:
    if explicit is not None:
        return int(explicit)
    if len(args) > index:
        try:
            return int(args[index])
        except ValueError:
            # ``enum('a','b')`` answers here: an argument that is not a number is
            # not a precision, and guessing one would be worse than having none.
            return None
    return None


#: The numeric cast grammar: ``DECIMAL(p)`` / ``DECIMAL(p,s)``, always with a precision.
_CAST_DECIMAL = re.compile(r"^\s*dec(?:i)?mal\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", re.I)
#: The character bases a cast name may ask for, and the one type they all narrow to:
#: SQL Server's own answer, which is the only engine that answers in characters at all.
_CAST_CHARACTER_BASES = frozenset({"nvarchar", "nchar", "varchar", "char"})


def cast_sqlalchemy_type(cast: str) -> Optional[TypeEngine]:
    """What a cast-name string means as a SQLAlchemy type, for both spellings to agree.

    The compiler lane builds statements through SQLAlchemy and the policy in this
    module speaks SQL type names, so the names one parse of *this* function has to
    cover are exactly what an engine's ``agg_cast``, ``group_key_cast`` and
    ``like_cast`` may answer — today ``DECIMAL(38, 10)`` and ``DECIMAL(38, 0)`` from
    SQL Server's averages and sums, and ``NVARCHAR(450)`` and ``NVARCHAR(4000)`` from
    its grouping and matching. A name outside the grammar is ``None``, which every
    caller reads as "cast nothing", and the grammar is not widened on a guess: an
    engine that starts answering a new spelling adds it here, beside the module that
    answers it. ``test_relational_engine_types`` enumerates the engines' own answers
    and fails on one this parser cannot read.

    ``DECIMAL`` and not its ``Numeric`` parent because on the mssql dialect ``Numeric``
    renders ``NUMERIC`` — a synonym to the server and a difference to anyone comparing
    the emitted SQL against a test.
    """
    stated = str(cast or "")
    match = _CAST_DECIMAL.match(stated)
    if match:
        return DECIMAL(int(match.group(1)), int(match.group(2) or 0), asdecimal=True)
    declared = parse_declared_type(stated)
    if declared.base in _CAST_CHARACTER_BASES:
        return NVARCHAR(declared.precision)
    return None
