"""
The WHERE grammar: precedence, every leaf operator, and the quoting this repo emits.

Ported from the iBase bridge's ``predicate.py``, so these cases press the same
judgements the reference's do — plus the ones the reference never had to make. Its
statement text was Kineviz's, which writes a bare ``n.name``; this proxy's comes from
``drivers/dialect.py``, which wraps every label, type and property in backticks through
``backtick()`` and doubles a backtick inside one. A parser that reads only unquoted
names works on a demo schema and fails on a warehouse whose table is called ``Order``
and whose column is called ``person id``, so both spellings are asserted here.

The last group is the port's whole point: the reference compiled its tree to T-SQL
inside the same module. Nothing here may.
"""

from __future__ import annotations

import inspect
import re
from typing import List

import pytest

from kineviz_database_proxy.drivers.relational import predicate, translate
from kineviz_database_proxy.drivers.relational.predicate import (
    And,
    BoolProp,
    Cmp,
    EntityFn,
    IdIn,
    InList,
    IsNull,
    Lit,
    Not,
    Or,
    Prop,
    StrOp,
    parse,
    parse_expression,
    split_name,
    unquote,
    walk,
)


def one(text: str) -> object:
    """Parse a WHERE body that must parse, and say which node came back if it did not."""
    expr = parse(text)
    assert expr is not None, "did not parse: %r" % text
    return expr


# ----- precedence and nesting -----


def test_or_is_the_loosest_binder():
    expr = one("a.x = 1 OR b.y = 2 AND c.z = 3")
    assert isinstance(expr, Or) and len(expr.parts) == 2
    assert expr.parts[0] == Cmp(Prop("a", "x"), "=", Lit(1))
    assert isinstance(expr.parts[1], And) and len(expr.parts[1].parts) == 2


def test_a_chain_of_and_flattens_into_one_node_rather_than_a_right_spine():
    expr = one("a.x = 1 AND b.y = 2 AND c.z = 3")
    assert isinstance(expr, And)
    assert len(expr.parts) == 3


def test_not_covers_one_predicate_not_the_rest_of_the_conjunction():
    expr = one("NOT a.x = 1 AND b.y = 2")
    assert isinstance(expr, And) and len(expr.parts) == 2
    assert isinstance(expr.parts[0], Not)
    assert isinstance(expr.parts[0].expr, Cmp)
    assert isinstance(expr.parts[1], Cmp)


def test_parentheses_rebuild_the_tree_underneath():
    expr = one("(a.x = 1 OR b.y = 2) AND c.z = 3")
    assert isinstance(expr, And) and len(expr.parts) == 2
    assert isinstance(expr.parts[0], Or)
    assert isinstance(expr.parts[1], Cmp)
    assert isinstance(one("a.x = 1 OR (b.y = 2 AND c.z = 3)"), Or)


def test_deeply_mixed_nesting():
    expr = one("NOT (a.x = 1 AND (b.y = 2 OR NOT c.z = 3)) OR d.w = 4")
    assert isinstance(expr, Or) and len(expr.parts) == 2
    left = expr.parts[0]
    assert isinstance(left, Not)
    inner = left.expr
    assert isinstance(inner, And) and len(inner.parts) == 2
    assert isinstance(inner.parts[1], Or)
    assert isinstance(inner.parts[1].parts[1], Not)


def test_two_parenthesised_groups_joined_by_and():
    expr = one("(n.a = 1) AND (n.b = 2)")
    assert isinstance(expr, And)
    assert expr == And([Cmp(Prop("n", "a"), "=", Lit(1)), Cmp(Prop("n", "b"), "=", Lit(2))])


# ----- every leaf operator -----


@pytest.mark.parametrize(
    "text,op",
    [("n.a = 1", "="), ("n.a <> 1", "<>"), ("n.a != 1", "<>"), ("n.a < 1", "<"),
     ("n.a > 1", ">"), ("n.a <= 1", "<="), ("n.a >= 1", ">=")],
)
def test_comparison_operators(text, op):
    expr = one(text)
    assert isinstance(expr, Cmp)
    assert expr.op == op
    assert expr.lhs == Prop("n", "a")
    assert expr.rhs == Lit(1)


def test_cross_variable_comparison_keeps_both_sides_as_properties():
    assert one("n.name = m.title") == Cmp(Prop("n", "name"), "=", Prop("m", "title"))


@pytest.mark.parametrize(
    "text,op,value",
    [("n.a CONTAINS 'bo'", "contains", "bo"),
     ("n.a STARTS WITH 'bo'", "startswith", "bo"),
     ("n.a ENDS WITH 'bo'", "endswith", "bo"),
     ("n.a =~ 'b.o'", "regex", "b.o")],
)
def test_string_operators(text, op, value):
    expr = one(text)
    assert isinstance(expr, StrOp) and expr.op == op and expr.value == value
    assert expr.lhs == Prop("n", "a")


def test_the_string_operators_survive_their_extra_space():
    assert one("n.a STARTS  WITH 'x'").op == "startswith"
    assert one("n.a ENDS  WITH 'x'").op == "endswith"


def test_in_list_keeps_values_and_types_in_order():
    assert one("n.a IN [1, 'two', 3.5, true, null]") == InList(Prop("n", "a"), [1, "two", 3.5, True, None])


def test_negated_in():
    expr = one("n.a NOT IN [1,2]")
    assert isinstance(expr, InList) and expr.negated and expr.values == [1, 2]


def test_not_before_the_whole_predicate_is_a_negation_not_a_negated_in():
    for text in ("NOT n.a IN [1,2]", "NOT(n.a IN [1,2])", "NOT ( n.a IN [1,2] )"):
        expr = one(text)
        assert isinstance(expr, Not), text
        assert isinstance(expr.expr, InList) and not expr.expr.negated, text


def test_in_unnest_is_the_same_list_with_a_spanner_spelling():
    assert one("n.a IN UNNEST([1,2])") == InList(Prop("n", "a"), [1, 2])


def test_an_empty_list_is_an_empty_list_not_a_missing_one():
    expr = one("n.a IN []")
    assert isinstance(expr, InList) and expr.values == []


def test_is_null_and_is_not_null():
    assert one("n.a IS NULL") == IsNull(Prop("n", "a"), negated=False)
    assert one("n.a IS NOT NULL") == IsNull(Prop("n", "a"), negated=True)


def test_bare_boolean_property():
    assert one("n.isfraud") == BoolProp(Prop("n", "isfraud"))


def test_an_entity_function_is_a_comparable_operand():
    assert one('NOT(type(r) IN ["PERFORMS"])') == Not(InList(EntityFn("r", "type"), ["PERFORMS"]))


def test_a_type_predicate_written_without_parentheses_reads_the_same_way():
    assert one("NOT type(r) IN ['PERFORMS']") == Not(InList(EntityFn("r", "type"), ["PERFORMS"]))


@pytest.mark.parametrize(
    "text",
    ["n", "n.a =", "'x'", "n.a = = 1", "(n.a = 1", "n.a ~ 1", "n.a = 1 2", "n.a IS", "type(r)"],
)
def test_things_that_are_not_predicates(text):
    assert parse(text) is None
    with pytest.raises(predicate.ParseError):
        parse_expression(text)


def test_a_bare_variable_is_refused_rather_than_read_as_a_whole_row():
    # The reference compiled this as a comparison against a column it could not name.
    with pytest.raises(predicate.ParseError) as caught:
        parse_expression("n AND m.x = 1")
    assert "n" in str(caught.value)


def test_a_parse_error_says_what_it_found():
    with pytest.raises(predicate.ParseError) as caught:
        parse_expression("n.a = ")
    assert str(caught.value)


# ----- case folding -----


def test_tolower_folds_a_literal():
    assert one("n.a = toLower('MiXeD')") == Cmp(Prop("n", "a"), "=", Lit("mixed"))


def test_toupper_folds_a_literal_the_other_way():
    assert one("n.a = upper('MiXeD')") == Cmp(Prop("n", "a"), "=", Lit("MIXED"))


def test_tolower_around_a_property_is_unwrapped_because_the_comparison_is_the_case_insensitive_part():
    assert one("toLower(n.name) CONTAINS 'ab'") == StrOp(Prop("n", "name"), "contains", "ab")


def test_folding_leaves_a_number_alone():
    assert one("n.age > toLower(18)") == Cmp(Prop("n", "age"), ">", Lit(18))


# ----- literals, escapes and the characters that matter to LIKE and to ids -----


@pytest.mark.parametrize(
    "text,expected",
    [("'a%b'", "a%b"), ('"a%b"', "a%b"), ("'a_b'", "a_b"), ("'a:b'", "a:b"),
     ("'50%_off:1'", "50%_off:1"), ("'it\\'s'", "it's"), ('"\\"q\\""', '"q"'),
     ("'back\\\\slash'", "back\\slash"), ("'tab\\there'", "tab\there")],
)
def test_literal_escapes(text, expected):
    assert one("n.a = " + text) == Cmp(Prop("n", "a"), "=", Lit(expected))


def test_a_double_quoted_literal_is_decoded_as_json_because_cypher_string_is_json_dumps():
    # ``dialect.cypher_string`` is ``json.dumps``, so a non-ASCII key arrives \u-escaped.
    assert one('n.key = "\\u00e9"') == Cmp(Prop("n", "key"), "=", Lit("é"))


def test_a_percent_an_underscore_and_a_colon_survive_an_in_list():
    assert one('n.id IN ["a%b", "c_d", "Person:42"]').values == ["a%b", "c_d", "Person:42"]


def test_a_keyword_inside_a_literal_does_not_end_the_predicate():
    expr = one("n.note = 'please RETURN and DELETE this' AND n.x = 1")
    assert isinstance(expr, And) and len(expr.parts) == 2
    assert expr.parts[0].rhs == Lit("please RETURN and DELETE this")


def test_negative_and_decimal_numbers():
    assert one("n.a > -1.5").rhs == Lit(-1.5)
    assert one("n.a <= -2").rhs == Lit(-2)


def test_true_false_and_null_are_literals():
    assert one("n.a = true").rhs == Lit(True)
    assert one("n.a <> false").rhs == Lit(False)
    assert one("n.a = null").rhs == Lit(None)


# ----- backtick-quoted identifiers, which is what this proxy emits -----


def test_a_quoted_property_with_spaces_is_one_property():
    assert one("n.`full name` = 'Avery Chen'") == Cmp(Prop("n", "full name"), "=", Lit("Avery Chen"))


def test_a_quoted_variable_and_a_quoted_property_together():
    assert one("`n`.`x` > 1") == Cmp(Prop("n", "x"), ">", Lit(1))


def test_a_doubled_backtick_inside_a_quoted_name_is_one_literal_backtick():
    # dialect.backtick("Or`der") is "`Or``der`", and that has to read back as the name.
    assert one("n.`Or``der` = 1") == Cmp(Prop("n", "Or`der"), "=", Lit(1))


def test_a_quoted_name_never_reads_as_a_keyword():
    expr = one("n.`and` = 1 AND n.`or` = 2 AND n.`in` = 3 AND n.`not` = 4")
    assert isinstance(expr, And) and len(expr.parts) == 4
    assert [part.lhs.prop for part in expr.parts] == ["and", "or", "in", "not"]


def test_a_quoted_name_can_be_a_clause_word_without_breaking_a_list():
    assert one("n.`limit` IN [1,2]").lhs == Prop("n", "limit")


def test_type_and_label_and_id_all_carry_their_variable():
    assert one("type(r) = 'WORKS_FOR'").lhs == EntityFn("r", "type")
    assert one("label(n) = 'Person'").lhs == EntityFn("n", "label")
    assert one("labels(n) = 'Person'").lhs == EntityFn("n", "labels")
    assert one("type(`r`) = 'X'").lhs == EntityFn("r", "type")


def test_an_identity_predicate_is_recognised_as_one():
    assert one("id(n) IN [internal_id(7, 42), internal_id(7, 43)]") == IdIn("n", [(7, 42), (7, 43)])
    assert one("id(n) NOT IN [internal_id(7, 42)]") == IdIn("n", [(7, 42)], negated=True)
    assert one("ID(n) IN [internal_id(1, 2)]") == IdIn("n", [(1, 2)])
    # A NOT ahead of the whole predicate negates it; it is not the IN's own negation.
    assert one("NOT id(n) IN [internal_id(7, 42)]") == Not(IdIn("n", [(7, 42)]))


def test_identity_literals_without_internal_id_stay_a_value_list_the_refusal_can_quote():
    expr = one('id(n) IN ["Person:42"]')
    assert isinstance(expr, InList) and isinstance(expr.lhs, EntityFn) and expr.values == ["Person:42"]


# ----- unquote and split_name, which translate.py shares -----


@pytest.mark.parametrize(
    "text,expected",
    [("Person", "Person"), ("`Person`", "Person"), ("`a``b`", "a`b"), ("'x'", "x"),
     ('"x"', "x"), ("`a b`", "a b"), ("", ""), ("`unbalanced", "`unbalanced")],
)
def test_unquote(text, expected):
    assert unquote(text) == expected


@pytest.mark.parametrize(
    "text,parts",
    [("n.name", ["n", "name"]), ("n.`full name`", ["n", "`full name`"]),
     ("`n`.`x`", ["`n`", "`x`"]), ("n.`a.b`", ["n", "`a.b`"]), ("n", ["n"]),
     ("n.`Or``der`", ["n", "`Or``der`"])],
)
def test_split_name_splits_on_unquoted_dots_only(text, parts):
    assert split_name(text) == parts


def test_a_dot_inside_a_quoted_property_is_not_a_path():
    assert one("n.`a.b` = 1") == Cmp(Prop("n", "a.b"), "=", Lit(1))


def test_a_nested_property_path_is_refused_rather_than_truncated():
    assert parse("n.a.b = 1") is None


# ----- walk -----


def test_walk_yields_every_node_parents_first():
    nodes: List[object] = list(walk(one("a.x = 1 AND NOT b.y IN [2]")))
    assert [type(node).__name__ for node in nodes] == ["And", "Cmp", "Prop", "Lit", "Not", "InList", "Prop"]
    assert list(walk(None)) == []


def test_walk_reaches_an_identity_hidden_in_a_leaf_operand():
    found = list(walk(one("id(n) IN [internal_id(1,2)] OR type(r) = 'X'")))
    assert any(isinstance(node, IdIn) for node in found)
    assert any(isinstance(node, EntityFn) and node.func == "type" for node in found)


def test_walk_finds_every_property_a_predicate_names():
    expr = one("n.a = 1 AND (m.b > 2 OR NOT c.c IS NULL)")
    assert [(node.var, node.prop) for node in walk(expr) if isinstance(node, Prop)] == [
        ("n", "a"), ("m", "b"), ("c", "c")
    ]


# ----- the port's point: no SQL anywhere in these modules -----

_IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+\S+")


@pytest.mark.parametrize("module", [predicate, translate])
def test_the_grammar_never_emits_sql_and_never_imports_a_dialect(module):
    imports = _IMPORT_LINE.findall(inspect.getsource(module))
    assert not [line for line in imports if "tsql" in line], imports
    assert not [line for line in imports if "dialect" in line], imports
    assert not [line for line in imports if "sqlalchemy" in line], imports


@pytest.mark.parametrize("module", [predicate, translate])
def test_no_sql_shaped_name_survives_in_the_module_namespace(module):
    for name in ("to_sql", "SqlCtx", "tsql", "Dialect", "placeholder", "like_pattern", "id_list_sql"):
        assert not hasattr(module, name), name


def test_an_expression_is_only_data():
    expr = one("n.a = 1 AND type(r) IN ['X']")
    kinds = (And, Or, Not, Cmp, StrOp, InList, IsNull, BoolProp, IdIn, EntityFn, Prop, Lit)
    assert all(isinstance(node, kinds) for node in walk(expr))
    assert all(not hasattr(node, "params") for node in walk(expr))
