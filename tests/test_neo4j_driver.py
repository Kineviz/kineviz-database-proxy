"""
The Neo4j driver's pure parts, and the Memgraph subclass's three deliberate
differences.

No server is dialled: what is asserted is the URI the driver would open, the row
cap it applies, and the full-text statements — the places where a wrong answer is
a bug the user sees rather than a connection error.
"""

from __future__ import annotations

import socket

import pytest

from kineviz_database_proxy.contract.intents import FulltextSearchRequest
from kineviz_database_proxy.drivers.bolt_schema import load_memgraph_schema, load_neo4j_schema
from kineviz_database_proxy.drivers.dialect import MEMGRAPH_DIALECT, NEO4J_DIALECT
from kineviz_database_proxy.drivers.memgraph import MemgraphDriver
from kineviz_database_proxy.drivers.neo4j import (
    CONNECTION_ACQUISITION_TIMEOUT_SECONDS,
    CONNECTION_TIMEOUT_SECONDS,
    prefer_ipv4,
    DEFAULT_FULLTEXT_INDEX,
    MAX_QUERY_RESULTS,
    Neo4jDriver,
    build_apoc_search_pattern,
    build_bolt_uri,
    build_search_pattern,
    build_search_statement,
    enforce_limit,
)
from kineviz_database_proxy.models.project import DatabaseConfig, DatabaseType, Project


def project(database_type: DatabaseType, **config) -> Project:
    return Project(
        id="p1",
        name="p1",
        database_type=database_type,
        database_config=DatabaseConfig(type=database_type, **config),
    )


# ---------------------------------------------------------------------------
# build_bolt_uri
# ---------------------------------------------------------------------------


def test_a_plain_host_and_port_become_a_bolt_uri():
    assert build_bolt_uri("db.internal", 7687, use_tls=False) == "bolt://db.internal:7687"


def test_tls_uses_the_self_signed_tolerant_scheme():
    # +ssc rather than +s: a customer-hosted server behind this proxy usually has a
    # self-signed certificate, and +s would refuse it.
    assert build_bolt_uri("db.internal", 7687, use_tls=True) == "bolt+ssc://db.internal:7687"


def test_a_host_that_already_names_a_scheme_is_used_verbatim():
    # This is how an Aura connection string is given; it carries its own routing
    # and TLS decisions.
    aura = "neo4j+s://abc123.databases.neo4j.io"
    assert build_bolt_uri(aura, 7687, use_tls=False) == aura


def test_a_host_that_carries_its_own_port_keeps_it():
    assert build_bolt_uri("db.internal:7688", 7687, use_tls=False) == "bolt://db.internal:7688"


def test_the_default_port_fills_in():
    assert build_bolt_uri("db.internal", None, use_tls=False) == "bolt://db.internal:7687"


def test_an_empty_host_is_refused_rather_than_dialled():
    with pytest.raises(ValueError):
        build_bolt_uri("", 7687, use_tls=False)


# ---------------------------------------------------------------------------
# enforce_limit
# ---------------------------------------------------------------------------


def test_a_returning_statement_without_a_limit_is_capped():
    assert enforce_limit("MATCH (n) RETURN n", 50) == "MATCH (n) RETURN n LIMIT 50"


def test_a_statement_that_caps_itself_is_left_alone():
    assert enforce_limit("MATCH (n) RETURN n LIMIT 5", 50) == "MATCH (n) RETURN n LIMIT 5"


def test_a_non_returning_statement_is_left_alone():
    assert enforce_limit("CALL db.schema.visualization()", 50) == "CALL db.schema.visualization()"


def test_a_trailing_semicolon_is_stripped_before_the_cap():
    assert enforce_limit("MATCH (n) RETURN n ;", 50) == "MATCH (n) RETURN n LIMIT 50"


# ---------------------------------------------------------------------------
# full-text search
# ---------------------------------------------------------------------------


def test_a_bare_word_becomes_a_regex_so_it_matches_as_a_substring():
    assert build_search_pattern("ada") == "/(.+)?ada(.+)?/"


def test_a_cjk_phrase_is_quoted():
    # Neo4j tokenizes every CJK character separately, so an unquoted phrase would
    # match any one of them.
    assert build_search_pattern("北京大学") == '"北京大学"'


def test_a_phrase_with_spaces_is_quoted_because_a_space_means_or():
    assert build_search_pattern("ada lovelace") == '"ada lovelace"'


def test_an_apostrophe_is_escaped():
    assert build_search_pattern("o'brien") == "o\\'brien"


def test_the_apoc_pattern_wildcards_the_punctuation_lucene_reserves():
    assert build_apoc_search_pattern("a.b-c") == "**a*b*c**"


def test_the_native_search_statement_pages_and_parameterises():
    statement, params = build_search_statement(
        FulltextSearchRequest(keyword="ada", limit=25, skip=50)
    )
    assert statement.startswith("CALL db.index.fulltext.queryNodes($indexName, $searchQuery) YIELD node ")
    assert statement.endswith("RETURN node SKIP 50 LIMIT 25")
    assert params == {"indexName": DEFAULT_FULLTEXT_INDEX, "searchQuery": "/(.+)?ada(.+)?/"}


def test_categories_narrow_the_search_to_those_label_sets():
    statement, _ = build_search_statement(
        FulltextSearchRequest(keyword="ada", categories=["Person", "Company"])
    )
    assert "WHERE labels(node) IN [['Person'],['Company']] " in statement


def test_count_only_returns_per_label_counts_instead_of_nodes():
    statement, _ = build_search_statement(FulltextSearchRequest(keyword="ada", countOnly=True))
    assert "RETURN labels(node) as label, count(node) as count ORDER BY label" in statement
    assert "RETURN node" not in statement


def test_the_apoc_route_calls_the_apoc_procedure_with_its_own_pattern():
    statement, params = build_search_statement(FulltextSearchRequest(keyword="a.b", useApoc=True))
    assert statement.startswith(f"CALL apoc.index.search($indexName, $searchQuery, {MAX_QUERY_RESULTS})")
    assert params["searchQuery"] == "**a*b**"


def test_a_named_index_overrides_the_default():
    _, params = build_search_statement(FulltextSearchRequest(keyword="ada", indexName="my_index"))
    assert params["indexName"] == "my_index"


# ---------------------------------------------------------------------------
# the two drivers
# ---------------------------------------------------------------------------


def test_neo4j_opens_the_configured_database():
    driver = Neo4jDriver(project(DatabaseType.NEO4J, host="db", database_id="analytics"))
    assert driver.database == "analytics"
    assert driver.uri == "bolt://db:7687"


def test_neo4j_with_no_database_configured_uses_the_server_default():
    assert Neo4jDriver(project(DatabaseType.NEO4J, host="db")).database is None


def test_memgraph_never_names_a_database():
    # Memgraph has no multi-database concept and errors on the session parameter,
    # so a configured value is ignored rather than passed through.
    driver = MemgraphDriver(project(DatabaseType.MEMGRAPH, host="db", database_id="ignored"))
    assert driver.database is None


def test_memgraph_uses_its_own_schema_probe():
    # db.schema.visualization() does not exist there; reusing Neo4j's probe left
    # every Memgraph project with no categories at all.
    assert Neo4jDriver.schema_loader is load_neo4j_schema
    assert MemgraphDriver.schema_loader is load_memgraph_schema


def test_memgraph_declares_no_full_text_search_but_the_same_statements():
    assert Neo4jDriver.graph_capabilities.fulltextSearch.supported is True
    assert "search" in (Neo4jDriver.graph_capabilities.intents or [])

    assert MemgraphDriver.graph_capabilities.fulltextSearch.supported is False
    assert "search" not in (MemgraphDriver.graph_capabilities.intents or [])

    # Same tokens, different name: Memgraph is bolt-compatible, so the traversal
    # statements are Neo4j's unchanged.
    assert MEMGRAPH_DIALECT.node_id_expr("n") == NEO4J_DIALECT.node_id_expr("n") == "ID(n)"
    assert MEMGRAPH_DIALECT.rel_type_expr("r") == "TYPE(r)"


def test_neither_backend_offers_a_database_switcher_through_the_proxy():
    # Neo4j has several databases; a proxy project pins one in its config and the
    # proxy exposes no route to switch, so the flag that makes the client offer a
    # switcher stays false. Memgraph has no multi-database concept in the first
    # place — same answer, different reason.
    assert Neo4jDriver.graph_capabilities.multiDatabase is False
    assert MemgraphDriver.graph_capabilities.multiDatabase is False


def test_the_api_info_advertises_search_only_where_it_exists():
    neo4j_urls = Neo4jDriver(project(DatabaseType.NEO4J, host="db")).get_api_info("p1")["api_urls"]
    memgraph_urls = MemgraphDriver(project(DatabaseType.MEMGRAPH, host="db")).get_api_info("p1")["api_urls"]

    assert neo4j_urls["query"] == "/api/neo4j/p1/query"
    assert "search" in neo4j_urls
    assert memgraph_urls["query"] == "/api/memgraph/p1/query"
    assert "search" not in memgraph_urls


# ---------------------------------------------------------------------------
# reaching the server, and failing to
# ---------------------------------------------------------------------------


async def test_the_driver_is_opened_with_a_connect_deadline(monkeypatch):
    # Left to itself the bolt driver waits 30s to connect and 60s to take a
    # connection from the pool, so a host that drops packets rather than refusing
    # them -- a firewall, a stopped container -- turns one /graphSchema into a
    # minute of nothing.
    import neo4j

    captured = {}

    def fake_driver(uri, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(neo4j.AsyncGraphDatabase, "driver", staticmethod(fake_driver))
    await Neo4jDriver(project(DatabaseType.NEO4J, host="db")).connect()

    assert captured["connection_timeout"] == CONNECTION_TIMEOUT_SECONDS
    assert captured["connection_acquisition_timeout"] == CONNECTION_ACQUISITION_TIMEOUT_SECONDS


def refusing(exception: Exception):
    async def _run(statement, parameters=None):
        raise exception

    return _run


async def test_a_statement_the_server_rejects_is_an_answer_not_a_fault():
    # `SHOW SCHEMA INFO` on a Memgraph without --schema-info-enabled, or the
    # schema procedures on something bolt-compatible that is not Neo4j.
    from neo4j.exceptions import ClientError

    driver = Neo4jDriver(project(DatabaseType.NEO4J, host="db"))
    driver._driver = object()
    driver._run = refusing(ClientError("There is no procedure with the name"))

    assert await driver._probe("CALL db.schema.nodeTypeProperties()") is None


async def test_an_unreachable_server_is_reported_rather_than_read_as_an_empty_graph():
    # Swallowing this one made /graphSchema answer success with zero categories,
    # which the client cannot tell from a database that is genuinely empty -- so
    # the user saw an empty panel instead of the connection error.
    from neo4j.exceptions import ServiceUnavailable

    driver = Neo4jDriver(project(DatabaseType.NEO4J, host="db"))
    driver._driver = object()
    driver._run = refusing(ServiceUnavailable("Unable to retrieve routing information"))

    response = await driver.get_graph_schema()
    assert response.success is False
    assert "routing information" in (response.error or "")


async def test_bad_credentials_stop_the_probe_instead_of_being_retried_per_statement():
    from neo4j.exceptions import AuthError

    driver = Neo4jDriver(project(DatabaseType.NEO4J, host="db"))
    driver._driver = object()
    driver._run = refusing(AuthError("The client is unauthorized due to authentication failure."))

    response = await driver.get_graph_schema()
    assert response.success is False
    assert "unauthorized" in (response.error or "").lower()


# ---------------------------------------------------------------------------
# prefer_ipv4
# ---------------------------------------------------------------------------


def address(host: str, port: int = 7687):
    from neo4j.addressing import Address

    return Address((host, port))


def host_name_of(resolved) -> str:
    """Public in neo4j 6, private in 5 — the resolver reads it either way."""
    return getattr(resolved, "host_name", None) or getattr(resolved, "_host_name", "")


def fake_getaddrinfo(monkeypatch, infos):
    from kineviz_database_proxy.drivers import neo4j as module

    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *args, **kwargs: infos)


V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 7687, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 7687))


def test_ipv4_is_tried_before_ipv6(monkeypatch):
    # `localhost` hands back ::1 first, and a container publishing on 127.0.0.1
    # has nothing there — connecting to it hangs rather than being refused, which
    # cost a full connect timeout per pooled connection.
    fake_getaddrinfo(monkeypatch, [V6, V4])
    assert [entry.host for entry in prefer_ipv4(address("localhost"))] == ["127.0.0.1", "::1"]


def test_the_configured_hostname_survives_so_tls_still_verifies_against_it(monkeypatch):
    # Returning bare addresses would make TLS check the certificate against
    # "127.0.0.1" rather than the name the project was configured with.
    fake_getaddrinfo(monkeypatch, [V6, V4])
    resolved = prefer_ipv4(address("db.example.com"))
    assert {host_name_of(entry) for entry in resolved} == {"db.example.com"}


def test_a_scoped_ipv6_address_is_dropped(monkeypatch):
    scoped = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 7687, 0, 12))
    fake_getaddrinfo(monkeypatch, [scoped, V4])
    assert [entry.host for entry in prefer_ipv4(address("localhost"))] == ["127.0.0.1"]


def test_a_host_that_does_not_resolve_is_handed_back_untouched(monkeypatch):
    from kineviz_database_proxy.drivers import neo4j as module

    def boom(*args, **kwargs):
        raise OSError("Name or service not known")

    monkeypatch.setattr(module.socket, "getaddrinfo", boom)
    original = address("nope.invalid")
    # The driver's own resolution reports the failure; second-guessing it here
    # would turn a clear DNS error into an empty address list.
    assert prefer_ipv4(original) == [original]
