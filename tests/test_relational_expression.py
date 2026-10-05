"""
The predicate AST as SQL: typed binds, escaped wildcards, and refusals that name things.

The two properties worth testing here are the ones that fail silently. A value pasted
into text is an injection and a plan-cache miss; a wildcard not escaped is a filter that
matches everything. Both look correct in the response.
"""

from __future__ import annotations

import datetime as _datetime
from typing import Dict, List

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql, mysql, postgresql

from graphxr_database_proxy.drivers.relational import expression, predicate
from graphxr_database_proxy.drivers.relational.engine import ServerInfo
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from graphxr_database_proxy.drivers.relational.expression import (
    BoundVariable,
    FanoutRequired,
    PredicateScope,
    UnknownProperty,
    UnknownVariable,
    UnsupportedPredicate,
    compile_predicate,
)

#: The mapping the fixtures stand on: one node table and one link table.
_COLUMNS: Dict[str, Dict[str, str]] = {
    "Person": {"person_id": "bigint", "name": "varchar(50)", "hired": "date", "isfraud": "bit"},
    "Employment": {"employment_id": "bigint", "person_id": "bigint", "organization_id": "bigint", "since": "datetime"},
}
_TYPES: Dict[str, str] = {"Person": "PERSON_TABLE", "Employment": "Employment"}
_KEYS = {"Person": "person_id", "Employment": "employment_id"}


def _scope(*, spelling=None, server=None) -> PredicateScope:
    spelling = spelling or PostgreSQLEngine()
    metadata = sa.MetaData()
    variables: List[BoundVariable] = []
    for entity, kind, var, alias_name in (
        ("Person", expression.VERTEX_KIND, "n", "v0"),
        ("Employment", expression.EDGE_KIND, "r", "e0"),
    ):
        columns = _COLUMNS[entity]
        table = sa.Table(_TYPES[entity], metadata, *[sa.Column(name, sa.String()) for name in columns])
        alias = table.alias(alias_name)
        variables.append(
            BoundVariable(
                var=var,
                entity=entity,
                kind=kind,
                columns={name: alias.c[name] for name in columns},
                types=dict(columns),
                key_prop=_KEYS[entity],
            )
        )
    return PredicateScope(variables, spelling=spelling, server=server)


def _where(text):
    return predicate.parse_expression(text)


def _statement(scope, condition):
    return sa.select(scope.by_var["n"].columns["person_id"]).where(condition)


def _sql(scope, condition, dialect=postgresql.dialect()) -> str:
    if condition is None:
        return ""
    return str(_statement(scope, condition).compile(dialect=dialect)).replace("\n", " ")


def _bound(scope, condition, dialect=postgresql.dialect()) -> Dict[str, object]:
    """
    The values that go over the wire.

    ``scope.parameters`` is what *this* compiler named and typed; ``LIKE`` is the exception,
    because ``autoescape`` builds its own parameter out of the escaped value. A test that
    asserts only on the text would pass with the escaping anywhere in it, so it reads both.
    """
    if condition is None:
        return {}
    return dict(_statement(scope, condition).compile(dialect=dialect).params)


# -- binds, not text --


def test_a_comparison_binds_the_value_and_never_writes_it_into_the_text():
    scope = _scope()
    condition = compile_predicate(_where("n.name = 'Avery'"), scope)
    assert "Avery" not in _sql(scope, condition)
    assert scope.parameters == {"gx_p1": "Avery"}


def test_a_number_is_bound_as_a_number_and_a_date_is_read_as_the_column_type():
    """
    A quoted date against a date column is a type error, not a match.

    The intent layer emits keys as statement text and the dialect quotes anything that is
    not numeric, so a ``DATE`` key arrives here as a string and has to become a date
    before it is bound.
    """
    scope = _scope()
    compile_predicate(_where("n.person_id = 1001"), scope)
    assert scope.parameters["gx_p1"] == 1001
    scope = _scope()
    compile_predicate(_where("n.hired = '2024-01-31'"), scope)
    assert scope.parameters["gx_p1"] == _datetime.date(2024, 1, 31)


def test_both_sides_of_a_comparison_may_be_a_variable():
    """A join written as a predicate compares two columns and binds nothing."""
    scope = _scope()
    condition = compile_predicate(_where("r.person_id = n.person_id"), scope)
    assert "e0.person_id = v0.person_id" in _sql(scope, condition)
    assert scope.parameters == {}


def test_a_null_comparison_stays_a_null_comparison():
    scope = _scope()
    condition = compile_predicate(_where("n.name IS NULL"), scope)
    assert "IS NULL" in _sql(scope, condition)
    scope = _scope()
    condition = compile_predicate(_where("n.name IS NOT NULL"), scope)
    assert "IS NOT NULL" in _sql(scope, condition)


def test_a_bare_boolean_property_is_bound_true_rather_than_compared_to_one():
    """``WHERE n.isfraud``: a ``bit``, a ``bool`` and a ``tinyint(1)`` each get their own."""
    scope = _scope()
    condition = compile_predicate(_where("n.isfraud"), scope)
    assert "isfraud =" in _sql(scope, condition)
    assert scope.parameters == {"gx_p1": True}


# -- LIKE, which is the one that lies --


@pytest.mark.parametrize(
    "value,escaped",
    [
        ("50%_x", "50/%/_x"),
        ("100%", "100/%"),
        ("a_b%c\\d", "a/_b/%c\\d"),
        # ``\`` is not the escape character here — ``ESCAPE '/'`` says / is — so it stays itself,
        # and / itself has to be doubled or a value holding / would eat the next character.
        (r"a\b", r"a\b"),
        ("a/b", "a//b"),
    ],
)
def test_a_like_value_with_wildcards_in_it_matches_the_wildcards_and_not_anything(value, escaped):
    """
    The bridge annotated its own ``like_pattern`` "not cosmetic" for this reason.

    ``CONTAINS '50%'`` that becomes ``LIKE '%50%%'`` answers every row holding ``50``, and
    the answer looks like data. The AST is built rather than parsed so the value is exactly
    the string at issue, and the assertion is on the bound value: the surrounding wildcards
    are in the statement text, so a value that came back with them added is a value that
    escaped nothing at all.
    """
    scope = _scope()
    condition = compile_predicate(predicate.StrOp(lhs=predicate.Prop("n", "name"), op="contains", value=value), scope)
    assert "ESCAPE '/'" in _sql(scope, condition)
    assert list(_bound(scope, condition).values()) == [escaped]


@pytest.mark.parametrize(
    "dialect,name",
    [(mssql.dialect(), "mssql"), (postgresql.dialect(), "postgresql"), (mysql.dialect(), "mysql")],
)
def test_the_escape_clause_is_emitted_on_every_engine_we_serve(dialect, name):
    scope = _scope(spelling=PostgreSQLEngine())
    condition = compile_predicate(_where("n.name CONTAINS '50%'"), scope)
    assert "ESCAPE" in _sql(scope, condition, dialect)
    assert list(_bound(scope, condition, dialect).values()) == ["50/%"]


def test_startswith_and_endswith_escape_the_same_way():
    scope = _scope()
    condition = compile_predicate(_where("n.name STARTS WITH 'a_b'"), scope)
    assert list(_bound(scope, condition).values()) == ["a/_b"]
    scope = _scope()
    condition = compile_predicate(_where("n.name ENDS WITH 'a%b'"), scope)
    assert list(_bound(scope, condition).values()) == ["a/%b"]


# -- regex --


def test_regex_is_refused_by_an_engine_that_has_no_operator_for_it():
    scope = _scope(spelling=MSSQLEngine())
    with pytest.raises(UnsupportedPredicate) as refused:
        compile_predicate(_where("n.name =~ 'abc'"), scope)
    message = str(refused.value).lower()
    assert "regular-expression" in message and "mssql" in message


def test_regex_binds_the_pattern_instead_of_writing_it_into_the_text():
    scope = _scope()
    condition = compile_predicate(_where("n.name =~ '^Avery'"), scope)
    assert "Avery" not in _sql(scope, condition)
    assert scope.parameters == {"gx_p1": "^Avery"}


def test_regex_uses_the_operator_the_engine_has():
    scope = _scope(spelling=PostgreSQLEngine())
    condition = compile_predicate(_where("n.name =~ '^A'"), scope)
    assert "= ~" in _sql(scope, condition) or " ~ " in _sql(scope, condition)
    scope = _scope(spelling=MySQLEngine())
    condition = compile_predicate(_where("n.name =~ '^A'"), scope)
    assert "REGEXP" in _sql(scope, condition, mysql.dialect())


# -- entity functions --


def test_a_relationship_type_test_collapses_to_the_branch_that_matches_it():
    """
    ``type(r)`` has no column: a relational table *is* the label.

    The collapse is what makes ``relationshipTypeFilter`` work — the client's hidden-type
    predicate ``NOT(type(r) IN [...])`` decides the branch, and a branch that is decided
    to be impossible costs nothing to run.
    """
    scope = _scope()
    assert str(compile_predicate(_where("type(r) = 'Employment'"), scope)) == "true"
    scope = _scope()
    assert str(compile_predicate(_where("type(r) = 'OTHER'"), scope)) == "false"


def test_a_negated_type_test_is_the_other_way_round():
    scope = _scope()
    condition = compile_predicate(_where("NOT (type(r) IN ['OTHER'])"), scope)
    assert str(condition) == "true"


def test_a_label_test_on_a_node_is_this_branchs_table():
    scope = _scope()
    assert str(compile_predicate(_where("labels(n) = 'Person'"), scope)) == "true"
    scope = _scope()
    assert str(compile_predicate(_where("labels(n) <> 'Person'"), scope)) == "false"


def test_a_property_named_label_is_still_a_property():
    """
    ``labels(n)`` is the entity test; ``n.label`` is a column that may or may not exist.

    Collapsing the two would let a table with a real ``label`` column be read as its own
    node label, which is the difference between a filter and a wrong answer.
    """
    scope = _scope()
    with pytest.raises(UnknownProperty):
        compile_predicate(_where("n.label = 'Person'"), scope)


def test_a_type_function_on_the_wrong_kind_of_variable_is_refused():
    scope = _scope()
    with pytest.raises(UnsupportedPredicate):
        compile_predicate(_where("type(n) = 'Person'"), scope)
    scope = _scope()
    with pytest.raises(UnsupportedPredicate):
        compile_predicate(_where("labels(r) = 'Employment'"), scope)


def test_an_identity_predicate_never_reaches_the_compiler():
    """``IdIn`` is the bolt shape; ``translate`` refuses it first, and this is the backstop."""
    scope = _scope()
    with pytest.raises(UnsupportedPredicate) as refused:
        compile_predicate(predicate.IdIn(var="n", refs=[(1, 2)]), scope)
    assert "ID(n)" in str(refused.value)


# -- names the plan does not have --


def test_a_variable_the_plan_never_bound_is_refused():
    scope = _scope()
    with pytest.raises(UnknownVariable):
        compile_predicate(_where("z.name = 'a'"), scope)


def test_a_property_that_is_not_a_column_is_refused_rather_than_guessed():
    """
    ``n.nickname`` on a table whose only name column is ``name``.

    The tempting guess is the near-miss column, and a filter that silently reads a
    different column returns rows nobody asked for.
    """
    scope = _scope()
    with pytest.raises(UnknownProperty) as refused:
        compile_predicate(_where("n.nickname = 'a'"), scope)
    assert "nickname" in str(refused.value)


def test_an_edge_property_is_read_off_the_link_table():
    scope = _scope()
    condition = compile_predicate(_where("r.since > '2024-01-01'"), scope)
    assert "since" in _sql(scope, condition)


def test_a_join_column_is_a_column_of_the_link_table_too():
    """Endpoint columns are not properties, but they are still what the row holds."""
    scope = _scope()
    condition = compile_predicate(_where("r.organization_id = 7"), scope)
    assert "organization_id" in _sql(scope, condition)


# -- key lists --


def test_a_small_key_list_is_an_inline_in_of_typed_binds():
    scope = _scope()
    condition = compile_predicate(_where("n.person_id IN [1001, 1002]"), scope)
    rendered = _sql(scope, condition)
    assert "IN (" in rendered
    assert scope.parameters == {"gx_p1": 1001, "gx_p2": 1002}
    assert "1001" not in rendered


def test_an_empty_list_is_a_constant_because_in_nothing_is_a_syntax_error():
    scope = _scope()
    assert str(compile_predicate(_where("n.person_id IN []"), scope)) == "false"
    scope = _scope()
    assert str(compile_predicate(_where("NOT (n.person_id IN [])"), scope)) == "true"


def test_a_big_selection_goes_over_as_one_array_parameter_on_postgres():
    server = ServerInfo(engine="postgresql", major=16, minor=4)
    scope = _scope(spelling=PostgreSQLEngine(), server=server)
    values = ", ".join(str(index) for index in range(150))
    condition = compile_predicate(_where("n.person_id IN [%s]" % values), scope)
    rendered = _sql(scope, condition)
    assert "ANY" in rendered
    assert len(scope.parameters) == 1
    assert list(scope.parameters.values())[0] == list(range(150))


def test_a_big_selection_on_sql_server_is_shredded_from_one_json_document():
    server = ServerInfo(engine="mssql", major=15, minor=0, compat_level=150)
    scope = _scope(spelling=MSSQLEngine(), server=server)
    values = ", ".join(str(index) for index in range(2001))
    condition = compile_predicate(_where("n.person_id IN [%s]" % values), scope)
    rendered = _sql(scope, condition, mssql.dialect())
    assert "OPENJSON" in rendered
    assert "WITH ([value] bigint" in rendered
    assert len([name for name in scope.parameters if name.startswith("gx_json")]) == 1


def test_a_server_too_old_for_the_bulk_form_is_told_so_and_cut_into_chunks():
    server = ServerInfo(engine="mssql", major=12, minor=0, compat_level=110)
    scope = _scope(spelling=MSSQLEngine(), server=server)
    values = ", ".join(str(index) for index in range(2001))
    expr = _where("n.person_id IN [%s]" % values)
    with pytest.raises(FanoutRequired):
        compile_predicate(expr, scope)
    slots = expression.fanout_slots(expr, scope)
    assert len(slots) == 1
    chunks = slots[0].chunks()
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 1]


def test_a_chunk_substitutes_into_the_tree_without_touching_the_rest_of_it():
    server = ServerInfo(engine="mssql", major=12, minor=0, compat_level=110)
    scope = _scope(spelling=MSSQLEngine(), server=server)
    expr = _where("n.person_id IN [1, 2, 3] AND n.name = 'a'")
    slots = expression.fanout_slots(expr, scope)
    assert slots == []  # three ids is not a fan-out
    rewritten = expression.replace_slot(expr, 0, [9, 8])
    assert isinstance(rewritten, predicate.And)
    scope = _scope()
    compile_predicate(rewritten, scope)
    assert scope.parameters["gx_p1"] == 9 and scope.parameters["gx_p2"] == 8


def test_only_the_slot_asked_for_is_replaced():
    expr = predicate.And(
        parts=[
            predicate.InList(lhs=predicate.Prop("n", "person_id"), values=[1, 2]),
            predicate.InList(lhs=predicate.Prop("n", "person_id"), values=[3, 4]),
        ]
    )
    scope = _scope()
    rewritten = expression.replace_slot(expr, 1, [9])
    compile_predicate(rewritten, scope)
    assert scope.parameters == {"gx_p1": 1, "gx_p2": 2, "gx_p3": 9}


def test_a_forced_fan_out_is_planned_per_slot_in_walk_order():
    """A predicate with two big lists fans out on both, and the caller runs the product."""
    server = ServerInfo(engine="mssql", major=12, compat_level=100)
    spelling = MSSQLEngine()
    scope = _scope(spelling=spelling, server=server)
    big = ", ".join(str(index) for index in range(2001))
    expr = predicate.And(
        parts=[
            predicate.InList(lhs=predicate.Prop("n", "person_id"), values=[index for index in range(2001)]),
            predicate.InList(lhs=predicate.Prop("n", "name"), values=[index for index in range(2001)]),
        ]
    )
    assert [slot.index for slot in expression.fanout_slots(expr, scope)] == [0, 1]
    assert big  # the text fixture and the tree fixture describe the same size list


def test_membership_of_a_relationship_type_list_needs_no_sql_at_all():
    scope = _scope()
    condition = compile_predicate(_where("type(r) IN ['Employment', 'OTHER']"), scope)
    assert str(condition) == "true"
