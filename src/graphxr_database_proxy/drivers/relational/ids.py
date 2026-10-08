"""
``<Label>:<key>`` — minted, decoded, and coerced back to the column's own type.

The identity these backends hand the client is built from a primary key rather than
read off an edge, which is what lets ``expand.predicate = "primary-key"`` work: the
same predicate that produced the id re-selects the row. ``drivers/intents.py`` already
splits such an id on the *first* colon, so a key containing colons survives, and that
function is reused rather than reimplemented — a second splitter with different rules
is how an id stops round-tripping.

Two traps this module exists to avoid, both inherited from the reference bridge:

* **a key never goes through ``float``**. ``float`` holds integers exactly to 2**53, so
  a ``bigint`` key of 9 007 199 254 740 993 would come back rounded, and two different
  rows would share an id. Anything integral is carried as ``int`` or ``Decimal`` and
  rendered with digits, never through a binary float.
* **``1.0`` is not ``1``**. A JavaScript client renders an integer key it has held as a
  float, so ``"Person:1.0"`` arrives for the row keyed ``1``. The declared column type
  decides: a key column that holds integers gets an ``int`` back.

Ids are strings end to end: minting never parses a key through a float, and decoding
never hands back anything but the text after the colon.
"""

from __future__ import annotations

import base64
import datetime as _datetime
import decimal
from typing import Optional, Tuple

from ..intents import split_label_key_id
from .engine import parse_declared_type

#: The separator between a label and a key in a minted id.
KEY_SEPARATOR = ":"

#: Declared base types that hold a whole number. Kept to the spellings the three
#: engines actually report (plus the normalised vocabulary's own names), because a
#: key typed ``double`` must not be narrowed to ``int`` by a guess.
INTEGER_TYPES = frozenset(
    {
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "mediumint",
        "int2",
        "int4",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "serial",
        "bigserial",
        "smallserial",
        "oid",
        "numeric",
        "decimal",
        "dec",
        "fixed",
        "number",
    }
)

#: Exact-but-not-necessarily-whole numerics: ``decimal(10,2)`` holds a fraction, so it
#: becomes a ``Decimal`` and never a float.
EXACT_TYPES = frozenset({"numeric", "decimal", "dec", "fixed", "number", "money", "smallmoney"})

#: Approximate numerics, where a float is what the column actually is.
APPROX_TYPES = frozenset({"float", "real", "double", "double precision", "float4", "float8"})

#: Types that mean "true or false" to these engines.
BOOLEAN_TYPES = frozenset({"bit", "bool", "boolean"})

#: Types that carry a moment rather than a number. ``parse_declared_type`` drops the
#: ``with time zone`` words, so the short bases are the whole list.
DATE_TYPES = frozenset({"date"})
TIME_TYPES = frozenset({"time", "timetz"})
TIMESTAMP_TYPES = frozenset({"datetime", "datetime2", "datetimeoffset", "smalldatetime", "timestamp", "timestamptz", "timestamp_ntz", "timestamp_tz", "timestamp_ltz"})


def _base(key_type: Optional[str]) -> str:
    """The declared base name, lower-cased and argument-free."""
    return parse_declared_type(key_type or "").base


def is_integral_type(key_type: Optional[str]) -> bool:
    """Whether a key column of this type holds whole numbers."""
    declared = parse_declared_type(key_type or "")
    if declared.base in INTEGER_TYPES:
        # ``numeric(10,2)`` and ``decimal(38,0)`` share a base with the integers: the
        # scale is what decides, not the name.
        return declared.scale in (None, 0)
    return False


def is_boolean_type(key_type: Optional[str]) -> bool:
    return _base(key_type) in BOOLEAN_TYPES


def key_text(value: object, key_type: Optional[str] = None) -> str:
    """
    A key as it appears inside a minted id.

    Rendering is where the float trap bites: an integral column whose value arrived as
    ``1.0`` (a client that could not tell an integer from a float) is written ``1``, so
    the id re-selects the row it came from. A ``bit`` key is written ``1``/``0`` for the
    same reason — that is what the column holds, and what the predicate has to compare.
    """
    if isinstance(value, bool):
        if key_type is None or is_boolean_type(key_type) or is_integral_type(key_type):
            return "1" if value else "0"
        return "true" if value else "false"
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, _datetime.datetime):
        return value.isoformat()
    if isinstance(value, (_datetime.date, _datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        if value.is_finite() and value == value.to_integral_value() and is_integral_type(key_type):
            # int conversion is exact and independent of Decimal's default
            # 28-digit context, including Snowflake NUMBER(38,0) keys.
            return str(int(value))
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and is_integral_type(key_type):
            return str(int(value))
        return repr(value)
    return str(value)


def mint(label: str, key: object, key_type: Optional[str] = None) -> Optional[str]:
    """
    ``<Label>:<key>``, or None when there is no key to mint from.

    A NULL key is a row this contract cannot name, so nothing is invented for it: the
    caller drops the element and says so rather than minting ``Person:None``, which
    would be one id shared by every keyless row.
    """
    if key is None:
        return None
    text = key_text(key, key_type)
    if not text:
        return None
    return "%s%s%s" % (label, KEY_SEPARATOR, text)


def decode(node_id: object) -> Optional[Tuple[str, str]]:
    """
    ``"Person:42"`` -> ``("Person", "42")``, splitting on the first colon.

    ``split_label_key_id`` is the project's existing splitter and the client's ids are
    read with it, so this is a call rather than a second implementation.
    """
    if node_id is None:
        return None
    return split_label_key_id(str(node_id))


def coerce_value(value: object, declared_type: Optional[str]) -> object:
    """
    A literal as something this column can be compared against, without pasting text.

    The intent layer emits key literals into statement *text* and the translator reads
    them back as Python values, so a ``bigint`` key arrives as ``int`` and a string key
    as ``str``. Two shapes still need help: a date key reaches us as ``"2024-01-01"``
    (quoted, because it is not numeric), and binding that text against a date column is
    a type error on mssql and postgresql rather than a match. A float that landed on an
    integer column is narrowed here, and never the other way round.

    Anything this cannot read is handed over unchanged, so the server reports the
    mismatch in its own words instead of us inventing a value that would have matched.
    """
    if value is None or declared_type is None:
        return value
    declared = parse_declared_type(declared_type)
    base = declared.base
    if isinstance(value, (list, tuple, dict)):
        return value
    if is_integral_type(declared_type):
        return _as_integral(value)
    if base in EXACT_TYPES and not isinstance(value, bool):
        return _as_decimal(value)
    if base in APPROX_TYPES:
        return _as_float(value)
    if isinstance(value, bool):
        return value
    if base in BOOLEAN_TYPES:
        return _as_bool(value)
    if isinstance(value, str):
        return _as_temporal(value, base)
    return value


def _as_integral(value: object) -> object:
    """``"1.0"``, ``1.0`` and ``Decimal("7")`` all mean row 7 on an integer column."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, decimal.Decimal):
        if value.is_finite() and value == value.to_integral_value():
            return int(value)
        return value
    try:
        parsed = decimal.Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return value
    if parsed.is_finite() and parsed == parsed.to_integral_value():
        return int(parsed)
    return value


def _as_decimal(value: object) -> object:
    """Exact numerics stay exact: text goes through ``Decimal``, never through ``float``."""
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return decimal.Decimal(value)
    try:
        # ``str`` first: ``Decimal(0.1)`` is 0.1000000000000000055511151231257827.
        return decimal.Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return value


def _as_float(value: object) -> object:
    if isinstance(value, float):
        return value
    if isinstance(value, bool):
        return value
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value


def _as_bool(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, (bytes, bytearray)):
        return any(value)
    text = str(value).strip().lower()
    if text in ("true", "t", "yes", "y", "1"):
        return True
    if text in ("false", "f", "no", "n", "0"):
        return False
    return value


def _as_temporal(value: str, base: str) -> object:
    """A quoted date, time or timestamp literal, read back as the type it names."""
    text = value.strip()
    if not text:
        return value
    try:
        if base in DATE_TYPES:
            return _datetime.date.fromisoformat(text)
        if base in TIME_TYPES:
            return _datetime.time.fromisoformat(text)
        if base in TIMESTAMP_TYPES:
            return _datetime.datetime.fromisoformat(text)
    except ValueError:
        return value
    return value


def coerce_key(text: object, key_type: Optional[str]) -> object:
    """
    The key half of a minted id, back as a value the column can be matched against.

    This is the decode direction of the round trip: ``Person:42`` -> ``("Person", "42")``
    -> ``42``. The declared type decides, which is why ids stay strings until a caller
    says which column they came from.
    """
    if text is None:
        return None
    if isinstance(text, (int, float, decimal.Decimal, bool, _datetime.date, _datetime.datetime, _datetime.time)):
        return coerce_value(text, key_type)
    return coerce_value(str(text), key_type)
