"""
The relational legacy-mode profile, pinned against the copy the client builds statements from.

`proxy/dialect.ts` in graphxr-dev is the second half of the mirror: it is what runs whenever
`GET /capabilities` is missing or the probe fails, so a token that disagrees with this proxy's
own dialect reaches the wire whether or not the capability record was ever read. The failure it
guards is documented in the client's own comment — the bolt `default` branch emits
`ID(n) IN ["<Label>:<key>", …]` against a key column and the engine prints every selected id
back at the user — and a silent drift in one token is that defect one field at a time.

Every client token below is transcribed from the TypeScript and reimplemented here rather than
imported from the proxy, so each comparison is between two answers and not a function and
itself. The backends come from `tests/test_relational_capability_record.py`, so neither file
gets to keep its own list.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from typing import Any, Dict, Sequence, Tuple

import pytest

from graphxr_database_proxy.drivers.dialect import (
    DIALECTS,
    MSSQL_DIALECT,
    MYSQL_DIALECT,
    POSTGRESQL_DIALECT,
    NodeRef,
    StatementDialect,
    build_expand,
    build_pull_category,
    dialect_for,
)
from graphxr_database_proxy.drivers.graph_support import (
    MSSQL_CAPABILITIES,
    MYSQL_CAPABILITIES,
    POSTGRESQL_CAPABILITIES,
)
from graphxr_database_proxy.drivers.intents import split_label_key_id
from graphxr_database_proxy.drivers.relational.translate import TranslateError, translate
from tests.test_relational_capability_record import RELATIONAL_BACKENDS

# -- the client's copy, transcribed ------------------------------------------------------

#: `shared/graphdb/identity.ts:167-178` — the table `renderKeyLiteral` reads.
CLIENT_NUMERIC_KEY_TYPES = {
    "int", "integer", "uint", "bigint", "smallint", "float", "double", "decimal", "number",
    "numeric", "int2", "int4", "int8", "real", "double precision", "float4", "float8", "oid",
    "money", "int64", "float64", "bignumeric", "int128", "int32", "int16", "uint64", "uint32",
    "uint16", "uint8", "serial",
}

#: `identity.ts:185` (strip from the first "("), `:189-192` and `:204-214`.
def _client_is_numeric_key_type(key_type: Any) -> bool:
    return re.sub(r"\s*\(.*$", "", str(key_type or "").lower()).strip() in CLIENT_NUMERIC_KEY_TYPES


def _client_render_key_literal(value: Any, key_type: Any) -> str:
    text = str(value)
    if _client_is_numeric_key_type(key_type) and re.match(r"^-?\d+(\.\d+)?$", text):
        return text
    return _client_cypher_string(text)


def _client_cypher_string(value: str) -> str:
    """`identity.ts:222-224` — `JSON.stringify(String(value))`."""
    return json.dumps(str(value))


def _client_backtick(name: str) -> str:
    """`identity.ts:227-229` — an embedded backtick is doubled."""
    return "`" + str(name).replace("`", "``") + "`"


def _client_bracket_list(literals: Sequence[str]) -> str:
    """`CypherDialect.ts:588-590`."""
    return "[" + ",".join(literals) + "]"


def _client_json_string_list(values: Sequence[str]) -> str:
    """`CypherDialect.ts:592-594`."""
    return "[" + ",".join(json.dumps(str(value)) for value in values) + "]"


def _client_skip_limit(skip: int, limit: int) -> str:
    """`tokens.ts:13`. The `| 0` truncates to 32 bits, which this proxy does not do; both
    answers are the same for any offset a canvas can ask for, and the difference is recorded
    in the findings rather than asserted as agreement."""
    return "SKIP {} LIMIT {}".format(int(skip), limit)


#: `proxy/dialect.ts:147-176`, `relationalProfile(backend)` — the booleans and strings.
CLIENT_PROFILE: Dict[str, Any] = {
    "predicate": "primary-key",
    "whereKeyword": "WHERE",
    "paramStyle": "inline",
    "relationshipFilter": "pattern",
    "returnVars": ["n", "r", "m"],
    "returnSeparator": ", ",
    "supportsMultiHop": True,
    "supportsHiddenRelationshipFilter": True,
    "supportsInternalRelationships": False,
    "supportsOnlyBetweenSelected": False,
    "supportsNodeIdPredicate": None,
    "supportsQuotedIdentifiers": None,
}

#: Proxy dialect field -> the client profile field it mirrors, or None with the reason there
#: is nothing to mirror. `test_every_dialect_field_is_compared_or_explained` makes this table
#: exhaustive, so a token added on either side has to be described here before this passes.
PROXY_FIELD_TO_CLIENT: Dict[str, Any] = {
    "name": "name",
    "predicate": "predicate",
    "node_id_expr": "nodeIdExpr",
    "rel_id_expr": "relIdExpr",
    "rel_type_expr": "relTypeExpr",
    "id_literal": "idLiteral",
    "id_list": "idList",
    "where_keyword": "whereKeyword",
    "relationship_filter": "relationshipFilter",
    "return_vars": "returnVars",
    "return_separator": "returnSeparator",
    "quote_identifier": "quoteIdentifier",
    "pagination": "pagination",
    "supports_multi_hop": "supportsMultiHop",
    "supports_only_between_selected": "supportsOnlyBetweenSelected",
    "key_access": "keyAccess",
    "key_literal": "keyLiteral",
    "key_list": "keyList",
    "label_pattern": "labelPattern",
    # Five with no mirror, each for its own reason:
    # a legacy profile has no way to project parts of a variable, and this dialect needs none
    # because its results carry whole records;
    "projection": None,
    # the label a pattern could not carry has nowhere to go in a token table — and here the
    # pattern always carries it;
    "label_predicate": None,
    "rel_endpoints_projection": None,
    # a link row carries its own endpoints, so one undirected statement is unambiguous here.
    # The client's `default` for this backend likewise emits `-[r]-`.
    "directed_patterns_only": None,
    # the per-engine SQL facts are `EngineSpelling`'s, deliberately not mirrored in a token table.
    "extra": None,
    # The client's `supportsQuotedIdentifiers` is this predicate's mirror, and it is unset.
    "pattern_identifier_ok": "supportsQuotedIdentifiers",
}

#: The client's profile fields a `StatementDialect` has no slot for, and where the fact lives
#: on this side instead.
CLIENT_ONLY_FIELDS: Dict[str, str] = {
    "stringList": 'the proxy inlines `["A","B"]` itself (dialect.py:494) — asserted below',
    "paramStyle": "only the client has a `WITH $params` form; a statement this proxy is handed is finished text",
    "withParamKeys": "part of that same `WITH $params` form",
    "omitEmptyParams": "part of that same `WITH $params` form",
    "relationshipTypeSeparator": "default `|`, which is what the proxy emits (dialect.py:206)",
    "supportsHiddenRelationshipFilter": "gated on `rel_type_expr` here (dialect.py:469) — asserted below",
    "supportsInternalRelationships": "a capability, not a token: `expand.internalRelationships` — asserted below",
    "supportsNodeIdPredicate": "unset, and the proxy's key path emits no node-id predicate either (dialect.py:488)",
    "internalRelationships": "the shape of a statement this backend refuses to build at all",
    "pull": "no overrides: the shared tokens already spell a pull the translator reads — asserted below",
    "finalize": "the client collapses whitespace in its own statement; the proxy joins its clause list",
}

#: One fixed argument per callable field, so two dialects can be compared by what they render.
CALL_SAMPLES: Dict[str, Tuple[Any, ...]] = {
    "node_id_expr": ("n",),
    "rel_type_expr": ("r",),
    "rel_id_expr": ("r",),
    "id_literal": ("7",),
    "id_list": (["a", "b"],),
    "quote_identifier": ("Order Item",),
    "pagination": (0, 10),
    "key_access": ("n", "first name"),
    "key_literal": ("1001", "INT64"),
    "key_list": (["1001", "1002"],),
    "label_pattern": ("n", "Person"),
    "pattern_identifier_ok": ("Order Item",),
}


def fingerprint(dialect: StatementDialect) -> Dict[str, Any]:
    """Every token of a dialect, callables reduced to what they render for a fixed input."""
    out: Dict[str, Any] = {}
    for field in fields(StatementDialect):
        value = getattr(dialect, field.name)
        if callable(value):
            value = value(*CALL_SAMPLES[field.name])
        out[field.name] = list(value) if isinstance(value, tuple) else value
    return out


@pytest.fixture(params=RELATIONAL_BACKENDS, ids=lambda item: item.value)
def dialect(request) -> StatementDialect:
    return DIALECTS[request.param.value]


# -- the comparison is complete ----------------------------------------------------------

def test_every_dialect_field_is_compared_or_explained() -> None:
    """A new token on either side has to be accounted for here before this file passes."""
    proxy_fields = {field.name for field in fields(StatementDialect)}
    assert proxy_fields == set(PROXY_FIELD_TO_CLIENT), {
        "unaccounted on the proxy side": sorted(proxy_fields - set(PROXY_FIELD_TO_CLIENT)),
        "no longer on the proxy side": sorted(set(PROXY_FIELD_TO_CLIENT) - proxy_fields),
    }
    mirrored = {value for value in PROXY_FIELD_TO_CLIENT.values() if value}
    assert not set(CLIENT_PROFILE) - mirrored - set(CLIENT_ONLY_FIELDS)


def test_the_profile_declares_nothing_this_dialect_has_not_agreed_to() -> None:
    """`supportsQuotedIdentifiers` and `supportsNodeIdPredicate` are unset, which for this
    profile means true and false; `adaptProxyStatement` strips quoting only when the first is
    strictly false, so a relational statement leaves the browser with its backticks on."""
    assert CLIENT_PROFILE["supportsQuotedIdentifiers"] is None
    assert MSSQL_DIALECT.pattern_identifier_ok("Order Item") is True
    assert MSSQL_DIALECT.pattern_identifier_ok("type(r)") is True


# -- the two tables agree ----------------------------------------------------------------

def test_the_scalars_of_the_two_tables_agree(dialect: StatementDialect) -> None:
    assert dialect.predicate == CLIENT_PROFILE["predicate"]
    assert dialect.where_keyword == CLIENT_PROFILE["whereKeyword"]
    assert dialect.relationship_filter == CLIENT_PROFILE["relationshipFilter"]
    assert list(dialect.return_vars) == CLIENT_PROFILE["returnVars"]
    assert dialect.return_separator == CLIENT_PROFILE["returnSeparator"]
    assert dialect.supports_multi_hop is CLIENT_PROFILE["supportsMultiHop"]
    assert dialect.supports_only_between_selected is CLIENT_PROFILE["supportsOnlyBetweenSelected"]


def test_the_callables_of_the_two_tables_render_the_same_text(dialect: StatementDialect) -> None:
    """A token is its output, so both sides are called with the same arguments. `Order Item`
    and ``a`b`` are the two names that break something on one side or the other, which is
    exactly why they are the samples — and `first name` is the key column the quoting in
    `e77c6a2` was paid for."""
    assert dialect.node_id_expr("n") == "id(n)"
    assert (dialect.rel_type_expr or (lambda _var: None))("r") == "type(r)"
    assert dialect.rel_id_expr is None
    assert dialect.id_literal("Person:1") == _client_cypher_string("Person:1")
    assert dialect.id_list(["a", "b"]) == _client_bracket_list(["a", "b"])
    assert dialect.quote_identifier("Order Item") == _client_backtick("Order Item")
    assert dialect.quote_identifier("a`b") == _client_backtick("a`b")
    assert dialect.pagination(0, 10) == _client_skip_limit(0, 10)
    assert dialect.pagination(10, 25) == _client_skip_limit(10, 25)
    assert dialect.key_access("n", "first name") == "n." + _client_backtick("first name")
    assert dialect.key_list(["1001", "1002"]) == _client_bracket_list(["1001", "1002"])
    assert dialect.label_pattern("n", "Order Item") == "n:" + _client_backtick("Order Item")
    assert dialect.projection is None and dialect.label_predicate is None
    assert dialect.rel_endpoints_projection is None


@pytest.mark.parametrize(
    "value,key_type",
    [
        (1001, "INT64"),
        ("1001", "int"),
        ("9007199254740993", "INT64"),
        ("abc", "INT64"),
        (7, "STRING"),
        ("2020-01-02", "DATE"),
        ("true", "BOOL"),
        ('{"a":1}', "JSON"),
        ("A:1", None),
        ("", "STRING"),
        ("007", "INT64"),
    ],
)
def test_the_key_literal_rule_is_one_rule_in_two_languages(value: Any, key_type: Any) -> None:
    """`render_key_literal` and `renderKeyLiteral` have to answer alike or a seed is quoted on
    one side of the wire and not the other, and a quoted integer stops matching its index."""
    for backend in RELATIONAL_BACKENDS:
        rendered = DIALECTS[backend.value].key_literal(value, key_type)
        assert rendered == _client_render_key_literal(value, key_type), (backend.value, rendered)


def test_the_numeric_key_type_table_is_the_same_table_on_both_sides() -> None:
    """The set behind both rules, compared rather than trusted: a type in the client's table
    and not the proxy's quotes a seed the client expects bare — the same bug, one column wide."""
    from graphxr_database_proxy.drivers.dialect import _NUMERIC_KEY_TYPES

    assert {name.lower() for name in _NUMERIC_KEY_TYPES} == CLIENT_NUMERIC_KEY_TYPES


def test_the_flags_the_profile_declares_are_decided_by_these_tokens_and_records(
    dialect: StatementDialect,
) -> None:
    """
    Three client booleans have no boolean on this side, so each is pinned to what actually
    decides it: a hidden type is excludable iff there is a `type(r)` to exclude with, and
    internal relationships are refused because the capability record says so — which is the
    same fact the client's `default` branch would have got wrong.
    """
    assert (dialect.rel_type_expr is not None) is CLIENT_PROFILE["supportsHiddenRelationshipFilter"]
    records = {
        "mssql": MSSQL_CAPABILITIES,
        "postgresql": POSTGRESQL_CAPABILITIES,
        "mysql": MYSQL_CAPABILITIES,
    }
    from graphxr_database_proxy.drivers.graph_support import SNOWFLAKE_CAPABILITIES
    records["snowflake"] = SNOWFLAKE_CAPABILITIES
    record = records[dialect.name]
    assert record.expand.internalRelationships is CLIENT_PROFILE["supportsInternalRelationships"]
    assert record.expand.multiHop is CLIENT_PROFILE["supportsMultiHop"]
    assert "both" not in record.expand.directions


# -- the statements both ends agree on ---------------------------------------------------

SEEDS = [
    NodeRef(category="Person", key_prop="id", key_value="1001"),
    NodeRef(category="Person", key_prop="id", key_value="1002"),
]

#: The client's own golden text, transcribed from `DatabaseProxyAdapter.test.ts:203-215` with
#: its `ticked()` helper (which is the backtick) already applied.
CLIENT_GOLDEN_ONE_HOP = (
    "MATCH (n:`Person`)-[r]-(m) WHERE n.`id` IN [1001,1002] RETURN n, r, m SKIP 0 LIMIT 10"
)


def test_the_proxy_builds_the_statement_the_clients_own_test_asserts(dialect: StatementDialect) -> None:
    built = build_expand(dialect, SEEDS, direction="all", key_types={"Person": "INT64"}, limit=10, skip=0)
    assert built == [CLIENT_GOLDEN_ONE_HOP], built[0]


def test_a_key_that_is_not_a_number_is_quoted_and_a_big_one_is_not(dialect: StatementDialect) -> None:
    """`DatabaseProxyAdapter.test.ts:225-235`: `<Label>:<key>` splits on the first colon, and a
    key above 2^53 has to arrive as digits — routed through a Number it would come back rounded
    and the expand would silently match a different row."""
    assert split_label_key_id("Person:A:1") == ("Person", "A:1")
    colon = build_expand(
        dialect, [NodeRef(category="Person", key_prop="id", key_value="A:1")],
        key_types={"Person": "STRING"}, limit=10, skip=0,
    )
    assert 'WHERE n.`id` IN ["A:1"]' in colon[0], colon[0]
    big = build_expand(
        dialect, [NodeRef(category="Person", key_prop="id", key_value="9007199254740993")],
        key_types={"Person": "INT64"}, limit=10, skip=0,
    )
    assert "WHERE n.`id` IN [9007199254740993]" in big[0], big[0]


def test_selected_types_go_in_the_pattern_and_hidden_ones_in_a_predicate(
    dialect: StatementDialect,
) -> None:
    """The combination the client's comment calls out as this backend's own: the filter is
    `pattern` *and* a hidden type is still excludable, because a type is a column of the link
    table rather than something the SQL has to know."""
    built = build_expand(
        dialect, SEEDS, direction="all", relationships=["KNOWS", "ACTED IN"],
        exclude_relationship_types=["HATES"], key_types={"Person": "INT64"}, limit=10, skip=0,
    )
    assert "-[r:`KNOWS`|`ACTED IN`]-(m)" in built[0], built[0]
    assert built[0].count("type(r)") == 1
    # The list the client renders with `jsonStringList`, spelled by this proxy's own builder.
    assert 'AND NOT(type(r) IN ' + _client_json_string_list(["HATES"]) + ")" in built[0], built[0]


def test_a_pull_is_the_shared_expand_tokens_with_no_override(dialect: StatementDialect) -> None:
    """`relationalProfile` declares no `pull`, and says why: the translator reads what the
    shared tokens already spell. This is that statement."""
    assert build_pull_category(dialect, "Person", limit=10, skip=0) == [
        "MATCH (n:`Person`) RETURN * SKIP 0 LIMIT 10"
    ]


def test_the_second_hop_names_its_variables_the_way_this_proxy_records_them(
    dialect: StatementDialect,
) -> None:
    """
    Pinned from this side only: the client's suite asserts nothing about a two-hop legacy
    statement, so there is no second copy of this text to compare with yet. Recorded so that
    when somebody does pin it over there, a difference is a finding rather than a surprise on
    a user's canvas.
    """
    built = build_expand(
        dialect, SEEDS, direction="from", hops=2, key_types={"Person": "INT64"}, limit=10, skip=0
    )
    assert built == [
        "MATCH (n:`Person`)-[r]->(n1)-[r1]->(m) WHERE n.`id` IN [1001,1002] "
        "RETURN n, r, m, n1, r1 SKIP 0 LIMIT 10"
    ], built[0]


def test_a_date_or_boolean_key_reaches_the_statement_quoted(dialect: StatementDialect) -> None:
    """
    The arrival half of the fourth duty. A `DATE` key is not numeric in either language's
    table, so it goes out as a string literal: correct as Cypher protocol, wrong as SQL — and
    the compiler binds that literal with the column's own type instead of pasting it. The
    binding half lives in `compile.py`, which is not this lane's to pin.
    """
    built = build_expand(
        dialect, [NodeRef(category="Employment", key_prop="started_on", key_value="2020-01-02")],
        key_types={"Employment": "DATE"}, limit=10, skip=0,
    )
    assert 'WHERE n.`started_on` IN ["2020-01-02"]' in built[0], built[0]


def test_a_bolt_shaped_identity_predicate_is_refused_by_name(dialect: StatementDialect) -> None:
    """
    The defect the whole mirror exists to prevent, and the half that lives here: a statement
    built from the client's `default` branch reaches this proxy and is refused by name — not
    answered with an empty canvas, and not with the selected ids printed back at the user.
    """
    statement = 'MATCH (n:`Person`)-[r]-(m) WHERE ID(n) IN ["Person:1001"] RETURN n, r, m'
    with pytest.raises(TranslateError) as refused:
        translate(statement)
    assert "ID(n) is not supported" in str(refused.value)


# -- the three engines really are one table ----------------------------------------------

def test_the_named_dialects_are_the_ones_the_registry_answers_for() -> None:
    """The three module constants the rest of the suite imports are the served objects."""
    for backend, constant in (
        ("mssql", MSSQL_DIALECT),
        ("postgresql", POSTGRESQL_DIALECT),
        ("mysql", MYSQL_DIALECT),
    ):
        assert DIALECTS[backend] is constant


def test_every_relational_dialect_is_one_token_table_apart_from_its_name() -> None:
    """
    Walked over the derived backends, not the three names, so a fourth engine that reuses the
    family helper trips the recorded-backend guard and one that brings its own tokens trips
    this. The per-engine differences (quoting, `TOP` / `ROW_NUMBER` / `LIMIT`, parameter
    ceilings) are the compiler's and live in `EngineSpelling`, so anything separating these
    tables would be an accident rather than a fact about the engines.
    """
    base = {
        key: value for key, value in fingerprint(DIALECTS[RELATIONAL_BACKENDS[0].value]).items() if key != "name"
    }
    for backend in RELATIONAL_BACKENDS:
        rendered = fingerprint(DIALECTS[backend.value])
        assert {key: value for key, value in rendered.items() if key != "name"} == base, backend.value


def test_the_url_segment_resolves_to_the_dialect_the_client_switches_on(
    dialect: StatementDialect,
) -> None:
    """`proxyLegacyProfile(backend)` and `proxyCapabilitiesFor(backend)` both switch on the URL
    segment; this is the same lookup on the same word."""
    assert dialect_for(dialect.name) is dialect
    assert dialect.name in {backend.value for backend in RELATIONAL_BACKENDS}
