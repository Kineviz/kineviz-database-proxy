"""
Identity: ``<Label>:<key>`` must survive being made, read, and made again.

The round trip is the invariant the whole expand surface rests on — ``intents.py``
re-selects a seed from the key half of an id, so an id that renders a key differently
than the column holds it does not widen a graph, it silently finds nothing.
"""

from __future__ import annotations

import datetime as _datetime
import decimal

import pytest

from graphxr_database_proxy.drivers.relational import ids
from graphxr_database_proxy.drivers.relational.ids import coerce_key, coerce_value, decode, key_text, mint


def test_a_minted_id_splits_back_into_the_label_and_the_key_it_came_from():
    identity = mint("Person", 1001, "bigint")
    assert identity == "Person:1001"
    assert decode(identity) == ("Person", "1001")


def test_a_key_containing_a_colon_survives_the_split():
    """``split_label_key_id`` splits on the *first* colon; a compound code keeps the rest."""
    identity = mint("Account", "INT-2024:0007:3", "varchar(32)")
    assert identity == "Account:INT-2024:0007:3"
    assert decode(identity) == ("Account", "INT-2024:0007:3")


def test_a_key_past_two_to_the_fifty_three_keeps_every_digit():
    """The JavaScript trap: a 64-bit key routed through a float comes back rounded."""
    big = 9007199254740993  # 2**53 + 1, the first integer a double cannot hold
    identity = mint("Order", big, "bigint")
    assert identity == "Order:9007199254740993"
    assert decode(identity)[1] == "9007199254740993"
    assert coerce_key(decode(identity)[1], "bigint") == big


def test_a_float_rendered_integer_is_re_read_as_the_integer_the_column_holds():
    """``Person:1.0`` arrives for row 1 when a client could not tell the two apart."""
    assert key_text(1.0, "bigint") == "1"
    assert mint("Person", 1.0, "bigint") == "Person:1"
    assert coerce_key("1.0", "bigint") == 1
    assert coerce_value("1.0", "int") == 1


def test_a_decimal_key_never_routes_through_float():
    """``Decimal("0.1")`` is not ``float(0.1)``: an exact column keeps its exactness."""
    value = decimal.Decimal("123456.78")
    assert coerce_value("123456.78", "numeric(10,2)") == value
    assert key_text(value, "numeric(10,2)") == "123456.78"
    assert isinstance(coerce_value("123456.78", "decimal(10,2)"), decimal.Decimal)


def test_a_key_that_is_nothing_mints_nothing():
    """A NULL key is a row this contract cannot name, so no id is invented for it."""
    assert mint("Person", None, "bigint") is None
    assert mint("Person", "", "varchar(20)") is None
    assert decode("Person:") is None
    assert decode("Person") is None
    assert decode(None) is None


def test_a_bit_key_is_written_the_way_the_column_holds_it():
    """``str(True)`` is ``"True"`` and no database stores that; ``1`` is what it stores."""
    assert mint("Flag", True, "bit") == "Flag:1"
    assert coerce_key("1", "bit") is True
    assert coerce_key("0", "bit") is False


def test_a_date_key_is_read_back_as_a_date_and_not_as_quoted_text():
    """
    The intent layer quotes a non-numeric key, so a date arrives as text.

    Binding that text against a date column is a type error on mssql and postgresql, not
    a match: the compiler has to read it back as the type the column declared.
    """
    identity = mint("Invoice", _datetime.date(2024, 1, 31), "date")
    assert identity == "Invoice:2024-01-31"
    assert coerce_key("2024-01-31", "date") == _datetime.date(2024, 1, 31)
    assert coerce_value("2024-01-31", "date") == _datetime.date(2024, 1, 31)
    assert coerce_value("2024-01-31 08:30:00", "datetime") == _datetime.datetime(2024, 1, 31, 8, 30, 0)


def test_a_time_key_is_read_back_as_a_time():
    assert coerce_value("08:30:00", "time") == _datetime.time(8, 30, 0)


def test_a_string_key_stays_a_string_even_when_it_looks_like_a_number():
    assert coerce_value("007", "varchar(20)") == "007"
    assert key_text("007", "varchar(20)") == "007"
    assert mint("Employee", "007", "varchar(20)") == "Employee:007"


def test_a_key_that_cannot_be_read_is_handed_over_rather_than_guessed():
    """The server's own type error names the column; a guessed value would just be wrong."""
    assert coerce_value("abc", "bigint") == "abc"
    assert coerce_value("not a date", "date") == "not a date"


def test_a_double_key_keeps_its_fraction():
    assert coerce_value("1.5", "double precision") == 1.5
    assert key_text(1.5, "double") == "1.5"
    assert mint("Reading", 1.5, "double") == "Reading:1.5"


def test_a_normalised_vocabulary_name_decides_the_same_as_the_engine_spelling():
    """``graph_key_types()`` reports the normalised names; the columns say the engine's."""
    assert coerce_key("42", "INT64") == 42
    assert coerce_key("42", "INT32") == 42
    assert key_text(42, "INT64") == "42"


def test_is_integral_type_asks_the_scale_and_not_only_the_name():
    assert ids.is_integral_type("bigint") is True
    assert ids.is_integral_type("decimal(38,0)") is True
    assert ids.is_integral_type("numeric(10,2)") is False
    assert ids.is_integral_type("varchar(20)") is False


def test_a_bytes_key_is_carried_as_base64_because_a_key_is_text():
    raw = b"\x01\x02\x03"
    identity = mint("Blob", raw, "varbinary(16)")
    assert identity is not None and identity.startswith("Blob:")
    assert decode(identity)[1] == "AQID"


@pytest.mark.parametrize("label,key,expected", [("Person", 1, "Person:1"), ("Order", 10, "Order:10")])
def test_minting_is_a_pure_function_of_the_label_and_the_key(label, key, expected):
    assert mint(label, key, "int") == expected
