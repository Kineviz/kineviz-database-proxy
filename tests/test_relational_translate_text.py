"""
The scanner guards: what counts as a word of its own, and what counts as a comment.

Lives with ``drivers/relational/translate_text.py``. These are the cases the
reference's ``\\bMATCH\\b`` / ``\\bRETURN\\b`` regexes got
wrong — a property named ``limit``, the word ``RETURN`` inside a literal, a
``WITH`` that belongs to ``STARTS WITH`` — so they are asserted on real statements
rather than on the scanner's private helpers.
"""

import pytest

from kineviz_database_proxy.drivers.relational.predicate import (
    And,
    Cmp,
    Lit,
    Prop,
    StrOp,
)
from kineviz_database_proxy.drivers.relational.translate import (
    TranslateError,
    clause_heads,
    parse_return,
    strip_comments,
    translate as plan_of,
)


def test_a_trailing_semicolon_and_a_line_comment_are_not_part_of_the_statement():
    plan = plan_of("MATCH (n:Person) RETURN n; // a note about the query\n")
    assert plan.vertices[0].label == "Person"
    assert plan.return_items == ["n"]
    assert strip_comments("MATCH (n) /* block */ RETURN n").split() == ["MATCH", "(n)", "RETURN", "n"]
    assert plan_of("MATCH (n) /* a note */ RETURN n LIMIT 3").limit == 3


def test_a_doubled_dash_is_not_a_comment_because_an_arrow_is():
    # ``-->`` is an unbracketed relationship, so treating ``--`` as a comment marker
    # would silently rewrite a pattern. Cypher's own line comment is ``//``.
    plan = plan_of("MATCH (n)-->(m) RETURN n, m")
    assert plan.edges[0].direction == "out" and plan.edges[0].types is None


def test_a_property_named_like_a_clause_is_not_a_clause():
    plan = plan_of("MATCH (n) WHERE n.limit > 3 AND n.offset < 2 RETURN n.count AS c LIMIT 5")
    assert plan.limit == 5
    assert plan.where == And([Cmp(Prop("n", "limit"), ">", Lit(3)), Cmp(Prop("n", "offset"), "<", Lit(2))])
    assert parse_return(plan.return_items).columns[0].alias == "c"


def test_a_keyword_inside_a_literal_is_not_a_clause():
    plan = plan_of("MATCH (n) WHERE n.note = 'RETURN me' AND n.x = 1 RETURN n LIMIT 2")
    assert plan.where.parts[0] == Cmp(Prop("n", "note"), "=", Lit("RETURN me"))
    assert plan.limit == 2


def test_starts_with_is_the_operator_and_not_a_with_clause():
    plan = plan_of("MATCH (n) WHERE n.name STARTS WITH 'Avery' RETURN n")
    assert plan.where == StrOp(Prop("n", "name"), "startswith", "Avery")


def test_a_word_that_is_part_of_a_longer_word_is_not_a_clause():
    assert clause_heads("MATCH (n:Reorder) RETURN n.order_id, n.dataset") == ["MATCH", "RETURN"]


def test_an_unterminated_literal_is_named_as_such():
    with pytest.raises(TranslateError) as caught:
        plan_of("MATCH (n) WHERE n.a = 'oops RETURN n")
    assert "unterminated" in str(caught.value)
