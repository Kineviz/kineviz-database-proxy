"""
What ``_require_bindable_key`` actually says, and what a person can do about it.

The refusal has two faces and they cost different fixes, so the message names which
one fired:

* a reader that wrote down no type at all (blank, ``STRING``, ``NULL``) means *the
  schema did not name one* — the only fix is to key on a column the schema can read;
* a reader that wrote down a real name this engine has no binding for means *the
  type is real, the engine cannot carry it* — the fix is to key on a column this
  engine can name, or to take the record out of the graph.

A link whose own key is ``None`` is not checked at all: ``convert.py`` mints its id
from the endpoint keys, not a bind, so there is nothing to refuse.

The assertions here pin only the parts of the sentence a person acts on: the
column, the label or type, the marker, and the remedy. The exact wording around them
is allowed to change without the tests going red.
"""

from __future__ import annotations

from typing import Tuple

import pytest

from graphxr_database_proxy.drivers.relational import compile
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine

#: Every engine behind this seam has a different vocabulary, so each refused example
#: is taken from that engine's own unbindable set. ``geometry`` is the one real-name
#: refused on all three (per ``test_relational_unnamed_type``'s ``REFUSED_BY_ENGINE``
#: enumeration); ``NULL`` and ``STRING`` are the "nobody looked" markers; blank is
#: the third.
NO_TYPE_MARKERS: Tuple[str, ...] = ("", "NULL", "STRING")


# -- the two faces of the refusal --------------------------------------------------------


@pytest.mark.parametrize("marker", NO_TYPE_MARKERS)
def test_a_marker_on_a_node_label_says_the_schema_did_not_name_one(marker: str) -> None:
    """
    A reader that wrote down blank / ``STRING`` / ``NULL`` is the schema saying *I have
    no type for this column*, not the engine refusing a real one. The message has to
    say so, and the remedy has to point at the schema (not at the engine), because
    nothing the engine can do will hand a type back.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(MSSQLEngine(), "node label", "Widget", "id", marker)
    message = str(refused.value)
    assert "Widget" in message and "id" in message, message
    # The marker case must not blame the engine for a missing name.
    assert "schema" in message, message
    # The fix has to be in the schema, not in the engine.
    assert "schema can name" in message, message


@pytest.mark.parametrize("marker", NO_TYPE_MARKERS)
def test_a_marker_on_a_relationship_type_says_the_schema_did_not_name_one(marker: str) -> None:
    """
    The same two-case split on the relationship path. A person keying an edge on a
    column nobody could read sees the same refusal as on the node side, with the same
    fix — the difference between "node label" and "relationship type" is only what
    the id is being made for.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(
            MSSQLEngine(), "relationship type", "OWNS", "rid", marker
        )
    message = str(refused.value)
    assert "OWNS" in message and "rid" in message, message
    assert "schema" in message, message
    assert "schema can name" in message, message


def test_a_real_name_an_engine_cannot_bind_is_named_and_the_remedy_names_the_engine() -> None:
    """
    A reader that wrote down ``geometry`` (a real name, but one every engine refuses
    to bind as a key) is the engine saying *I cannot carry this type*, not the schema
    saying *I have no answer*. The message has to name the type and blame the engine,
    because that is the part the user can act on by re-keying or excluding the record.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(
            MSSQLEngine(), "node label", "Geometry", "g", "geometry"
        )
    message = str(refused.value)
    assert "Geometry" in message and "g" in message and "geometry" in message, message
    # The real-name case must blame the engine (which is what the user has to act on).
    assert "this engine cannot bind" in message, message
    # The fix is on the engine side.
    assert "this engine can name" in message, message


def test_a_real_name_an_engine_cannot_bind_is_refused_on_a_relationship_type() -> None:
    """
    The other half of the real-name path: an edge whose key column is a type the
    engine cannot bind. Same message shape as the node-label case.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(
            PostgreSQLEngine(), "relationship type", "COVERS", "g", "geometry"
        )
    message = str(refused.value)
    assert "COVERS" in message and "g" in message and "geometry" in message, message
    assert "this engine cannot bind" in message, message
    assert "this engine can name" in message, message


# -- the remedy is named in both cases ----------------------------------------------------


def test_the_remedy_names_exclude_and_include_for_a_real_type() -> None:
    """
    The real-name case is the one a person most often reaches for ``properties`` to
    fix; the message has to point at the *actual* two ways out instead. Re-keying
    onto a column the engine can carry, and taking the record out of the graph
    (``exclude``, or clearing it from ``include``) are the only two that work, and
    the test pins both in the message so a re-wording that drops either one is
    caught.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(
            MySQLEngine(), "node label", "Pin", "p", "geometry"
        )
    message = str(refused.value)
    assert "exclude" in message and "include" in message, message


def test_the_remedy_names_exclude_and_include_for_a_marker_too() -> None:
    """
    The exclude/include escape hatch is also the only one for the marker case: a
    column nobody could type is no more bindable than a column typed ``geometry``,
    so the person who cannot fix the schema still has a way to take the record out.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(
            MSSQLEngine(), "node label", "Pin", "p", "NULL"
        )
    message = str(refused.value)
    assert "exclude" in message and "include" in message, message


# -- the link with no key of its own is not checked --------------------------------------


def test_a_link_with_no_key_of_its_own_is_not_checked_at_all() -> None:
    """
    An association table with no primary key of its own is not a candidate for
    refusal: ``convert.py`` mints the id from the two endpoint keys, not from a
    bind against the key column, so the guard's "engine has no name for the type"
    check has nothing to ask. A type even the engine cannot spell — let alone
    ``NULL`` — must not raise, because the only fix would be to invent a key the
    table does not have.
    """
    # No exception is the assertion: if any of these raised, the guard would be
    # firing on a path the driver never binds against.
    compile._require_bindable_key(MSSQLEngine(), "node label", "Seed", None, None)
    compile._require_bindable_key(MSSQLEngine(), "node label", "Seed", None, "NULL")
    compile._require_bindable_key(
        MSSQLEngine(), "relationship type", "ENROLLS", None, "geometry"
    )
    compile._require_bindable_key(
        PostgreSQLEngine(),
        "relationship type",
        "ENROLLS",
        None,
        "a-type-nobody-ever-heard-of",
    )


# -- the message keeps the mechanism in both cases ---------------------------------------


@pytest.mark.parametrize("marker", NO_TYPE_MARKERS)
def test_the_mechanism_clause_survives_a_marker_too(marker: str) -> None:
    """
    The "a seed could not be re-selected, so the lookup is refused rather than
    answered with an empty result" half is the part that explains *why* this is a
    refusal instead of a silent empty canvas, and a person needs it to know what
    fixing the column would have changed. Drop it from either case and the test
    fails — the half-line is the difference between a refusal and a bug report.
    """
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(MSSQLEngine(), "node label", "X", "k", marker)
    message = str(refused.value)
    assert "seed could not be re-selected" in message, message
    assert "refused" in message and "empty result" in message, message


def test_the_mechanism_clause_survives_a_real_name() -> None:
    """The same mechanism clause, on the real-name half."""
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(MSSQLEngine(), "node label", "X", "k", "geometry")
    message = str(refused.value)
    assert "seed could not be re-selected" in message, message
    assert "refused" in message and "empty result" in message, message


# -- the existing R8 test's two-substring assertion still holds --------------------------


def test_r8_assertion_substrings_still_hold_after_the_wording_change() -> None:
    """
    ``test_relational_unnamed_type.test_a_refused_type_that_a_mapping_may_still_key_on_is_refused_by_the_guard``
    asserts on two substrings of the old message: the declared type and the column
    name. The new wording keeps both, because it was written as a superset — the
    original "declared type %r ... cannot bind" sentence is still the case-(b)
    branch — and this test pins that, so a future re-wording that drops either
    substring goes red here too.
    """
    spelling = MSSQLEngine()
    # Pick one of the refused spellings R8's own test reaches for, so the assertion
    # is over the same code path.
    declared = "hierarchyid"
    assert spelling.column_type(declared) is None
    assert spelling.single_column_key_ok(declared)
    with pytest.raises(compile.UnbindableKey) as refused:
        compile._require_bindable_key(spelling, "node label", "Seed", "k", declared)
    message = str(refused.value)
    assert declared in message, message
    assert "k" in message, message
