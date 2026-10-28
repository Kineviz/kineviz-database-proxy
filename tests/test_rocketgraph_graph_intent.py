"""Tests for `_rewrite_for_graph_intent` — the openCypher-aware query
classifier and rewriter that decides whether RocketGraph should return a
graph or a table.
"""
from kineviz_database_proxy.drivers.rocketgraph import _rewrite_for_graph_intent


# ---------------------------------------------------------------------------
# Table intent: queries that should NOT be rewritten.
# ---------------------------------------------------------------------------

def test_table_when_property_access():
    q = "MATCH (n) RETURN n.name"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False
    assert out == q


def test_table_when_function_call():
    q = "MATCH (n) RETURN count(n)"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_aliased():
    q = "MATCH (n) RETURN n.acct_id AS id"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_with_clause():
    q = "MATCH (n) WITH n.name AS name RETURN name"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_unwind():
    q = "UNWIND [1, 2, 3] AS x RETURN x"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_no_match():
    q = "RETURN 1"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_optional_match_present():
    q = "MATCH (n) OPTIONAL MATCH (n)-[r]-(m) RETURN n, r, m"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_multiple_match_clauses():
    q = "MATCH (a) MATCH (b) RETURN a, b"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_return_has_unbound_var():
    q = "MATCH (n) RETURN n, unknown"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_table_when_p_is_node_variable():
    # User used `p` as a node variable — we'd collide if we tried to
    # introduce a path variable named `p`.
    q = "MATCH (p:Person) RETURN p"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_graph_return_star_with_anonymous_pattern():
    # Fully-anonymous patterns still bind to a path variable.
    q = "MATCH ()-[]-() RETURN *"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is True
    assert out == "MATCH p=()-[]-() RETURN p"


# ---------------------------------------------------------------------------
# Graph intent: queries that should be rewritten to MATCH p=... RETURN p.
# ---------------------------------------------------------------------------

def test_graph_single_node():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n) RETURN n")
    assert is_graph is True
    assert out == "MATCH p=(n) RETURN p"


def test_graph_node_with_label():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n:Person) RETURN n")
    assert is_graph is True
    assert out == "MATCH p=(n:Person) RETURN p"


def test_graph_node_edge_node():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n)-[r]->(m) RETURN n, r, m")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]->(m) RETURN p"


def test_graph_existing_path_var_renamed():
    out, is_graph = _rewrite_for_graph_intent("MATCH f=(n)-[r]->(m) RETURN f")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]->(m) RETURN p"


def test_graph_existing_path_var_named_p_kept():
    out, is_graph = _rewrite_for_graph_intent("MATCH p=(n)-[r]->(m) RETURN p")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]->(m) RETURN p"


def test_graph_with_where_clause():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (n) WHERE n.age > 18 RETURN n"
    )
    assert is_graph is True
    assert out == "MATCH p=(n) WHERE n.age > 18 RETURN p"


def test_graph_with_limit():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n) RETURN n LIMIT 5")
    assert is_graph is True
    assert out == "MATCH p=(n) RETURN p LIMIT 5"


def test_graph_with_where_and_limit():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (n:Person) WHERE n.age > 18 RETURN n LIMIT 5"
    )
    assert is_graph is True
    assert out == "MATCH p=(n:Person) WHERE n.age > 18 RETURN p LIMIT 5"


def test_graph_with_order_by_and_skip():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (n) RETURN n ORDER BY n.name SKIP 5 LIMIT 10"
    )
    assert is_graph is True
    assert out == "MATCH p=(n) RETURN p ORDER BY n.name SKIP 5 LIMIT 10"


def test_graph_return_star_simple_match():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n) RETURN *")
    assert is_graph is True
    assert out == "MATCH p=(n) RETURN p"


def test_graph_return_star_with_pattern():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n)-[r]->(m) RETURN *")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]->(m) RETURN p"


def test_graph_return_star_with_existing_path_var():
    out, is_graph = _rewrite_for_graph_intent("MATCH f=(n)-[r]->(m) RETURN *")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]->(m) RETURN p"


def test_graph_return_star_with_where_and_limit():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (n) WHERE n.age > 18 RETURN * LIMIT 5"
    )
    assert is_graph is True
    assert out == "MATCH p=(n) WHERE n.age > 18 RETURN p LIMIT 5"


def test_table_when_return_star_with_with_clause():
    # WITH is a forbidden clause — fall back to table even with RETURN *.
    q = "MATCH (n) WITH n RETURN *"
    out, is_graph = _rewrite_for_graph_intent(q)
    assert is_graph is False


def test_graph_lowercase_keywords_match_preserved():
    out, is_graph = _rewrite_for_graph_intent("match (n) return n")
    assert is_graph is True
    # Original "match" case preserved; RETURN normalized to uppercase.
    assert out == "match p=(n) RETURN p"


def test_graph_strips_trailing_semicolon():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n) RETURN n;")
    assert is_graph is True
    assert out == "MATCH p=(n) RETURN p"


def test_graph_with_typed_relationship():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (n:Person)-[r:Knows]->(m:Person) RETURN n, r, m"
    )
    assert is_graph is True
    assert out == "MATCH p=(n:Person)-[r:Knows]->(m:Person) RETURN p"


def test_graph_with_anonymous_endpoints():
    out, is_graph = _rewrite_for_graph_intent("MATCH (n)-[r]-() RETURN n, r")
    assert is_graph is True
    assert out == "MATCH p=(n)-[r]-() RETURN p"


def test_graph_multi_hop_path():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (a)-[r1]->(b)-[r2]->(c) RETURN a, b, c, r1, r2"
    )
    assert is_graph is True
    assert out == "MATCH p=(a)-[r1]->(b)-[r2]->(c) RETURN p"


def test_graph_variable_length_relationship():
    out, is_graph = _rewrite_for_graph_intent(
        "MATCH (a)-[r:KNOWS*1..3]->(b) RETURN a, r, b"
    )
    assert is_graph is True
    assert out == "MATCH p=(a)-[r:KNOWS*1..3]->(b) RETURN p"
