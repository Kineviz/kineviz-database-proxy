"""Tests for RocketGraph QueryParser.

The parser receives an explicit ``is_graph`` flag (decided upstream by the
query rewriter) — it doesn't re-derive the format from the response. These
tests cover the parser given that flag for both TABLE and GRAPH inputs.

Response shapes are based on actual RocketGraph output documented in
src/kineviz_database_proxy/drivers/recketGraphReturn.md.
"""
from kineviz_database_proxy.drivers.rocketgraph import QueryParser
from kineviz_database_proxy.models.project import GraphData, QueryData


def _node(node_id, name, key, properties):
    return {
        "id": node_id,
        "properties": properties,
        "metadata": {
            "name": name,
            "schema": [[k, "text"] for k in properties.keys()],
            "key": key,
        },
    }


def _edge(edge_id, name, properties, source_key, target_key, source_name, target_name):
    return {
        "id": edge_id,
        "properties": properties,
        "metadata": {
            "name": name,
            "schema": [[k, "text"] for k in properties.keys()],
            "source_name": source_name,
            "target_name": target_name,
            "source_key": source_key,
            "target_key": target_key,
        },
    }


def test_scalar_alias_column_returns_table():
    # MATCH (n) RETURN n.acct_id as id limit 2
    response = {
        "columns": ["id"],
        "data": [["80DF4BC90|B226947E0"], ["966AF4700|A11F08380"]],
    }
    result = QueryParser.parse(response, is_graph=False)

    assert isinstance(result, QueryData)
    assert result.type == "TABLE"
    # First row is the header; remaining rows are data.
    assert result.data == [
        ["id"],
        ["80DF4BC90|B226947E0"],
        ["966AF4700|A11F08380"],
    ]


def test_flattened_node_columns_returns_table():
    # MATCH (n) RETURN n limit 2 — RocketGraph flattens to n_<prop> columns
    response = {
        "columns": ["n_acct_id", "n_bank_number", "n_account_number"],
        "data": [
            ["9867AE7C0|B1FEF59B0", "9867AE7C0", "B1FEF59B0"],
            ["80957A420|81B175080", "80957A420", "81B175080"],
        ],
    }
    result = QueryParser.parse(response, is_graph=False)

    assert result.type == "TABLE"
    assert result.data == [
        ["n_acct_id", "n_bank_number", "n_account_number"],
        ["9867AE7C0|B1FEF59B0", "9867AE7C0", "B1FEF59B0"],
        ["80957A420|81B175080", "80957A420", "81B175080"],
    ]


def test_flattened_node_edge_node_returns_table():
    # MATCH (n)-[r]->(m) RETURN n, r, m — also a flat table (no path var)
    response = {
        "columns": ["n_acct_id", "r_amount_paid", "m_acct_id"],
        "data": [
            ["A|1", 80.12, "B|2"],
            ["C|3", 6449.27, "D|4"],
        ],
    }
    result = QueryParser.parse(response, is_graph=False)

    assert result.type == "TABLE"
    # First row is headers, then two data rows.
    assert len(result.data) == 3
    # r_amount_paid sits at column index 1 in data row 1.
    assert result.data[1][1] == 80.12


def test_empty_data_returns_table():
    response = {"columns": ["x"], "data": []}
    result = QueryParser.parse(response, is_graph=False)
    assert result.type == "TABLE"
    # Header row only.
    assert result.data == [["x"]]


def test_none_data_returns_empty_table():
    response = {"columns": [], "data": None}
    result = QueryParser.parse(response, is_graph=False)
    assert result.type == "TABLE"
    # Empty header row only.
    assert result.data == [[]]


def test_row_shorter_than_columns_is_padded():
    response = {
        "columns": ["a", "b", "c"],
        "data": [["x"]],
    }
    result = QueryParser.parse(response, is_graph=False)
    assert result.data == [["a", "b", "c"], ["x", None, None]]


def test_single_node_path_returns_graph():
    # MATCH p=(n) RETURN p
    response = {
        "columns": ["p"],
        "data": [[
            [_node(43980465661923, "Accounts", "acct_id",
                   {"acct_id": "89CAA90C0|96B392620", "bank_number": "89CAA90C0"})]
        ]],
    }
    result = QueryParser.parse(response, is_graph=True)

    assert result.type == "GRAPH"
    assert isinstance(result.data, GraphData)
    assert len(result.data.nodes) == 1
    assert len(result.data.relationships) == 0
    node = result.data.nodes[0]
    assert node.id == "43980465661923"
    assert node.labels == ["Accounts"]
    assert node.properties == {
        "acct_id": "89CAA90C0|96B392620",
        "bank_number": "89CAA90C0",
    }


def test_node_edge_node_path_returns_graph():
    # MATCH p=(n)-[r]->(m) RETURN p
    response = {
        "columns": ["p"],
        "data": [[
            [
                _node(43980465661923, "Accounts", "acct_id",
                      {"acct_id": "89CAA90C0|96B392620"}),
                _edge(39582419475023, "Transactions",
                      {"from_account_id": "89CAA90C0|96B392620",
                       "to_account_id": "8AA324680|98B63A0B0",
                       "amount_paid": 49.36},
                      source_key="from_account_id",
                      target_key="to_account_id",
                      source_name="Accounts",
                      target_name="Accounts"),
                _node(43980465645172, "Accounts", "acct_id",
                      {"acct_id": "8AA324680|98B63A0B0"}),
            ]
        ]],
    }
    result = QueryParser.parse(response, is_graph=True)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 2
    assert len(result.data.relationships) == 1

    node_ids = sorted(n.id for n in result.data.nodes)
    assert node_ids == ["43980465645172", "43980465661923"]

    edge = result.data.relationships[0]
    assert edge.id == "39582419475023"
    assert edge.type == "Transactions"
    assert edge.startNodeId == "43980465661923"
    assert edge.endNodeId == "43980465645172"
    assert edge.properties["amount_paid"] == 49.36


def test_multi_hop_path_returns_graph():
    # MATCH p=(a)-[r1]->(b)-[r2]->(c) RETURN p
    a = _node(1, "N", "id", {"id": "A"})
    b = _node(2, "N", "id", {"id": "B"})
    c = _node(3, "N", "id", {"id": "C"})
    r1 = _edge(10, "E", {}, source_key="src", target_key="dst",
               source_name="N", target_name="N")
    r2 = _edge(11, "E", {}, source_key="src", target_key="dst",
               source_name="N", target_name="N")

    response = {"columns": ["p"], "data": [[[a, r1, b, r2, c]]]}
    result = QueryParser.parse(response, is_graph=True)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 3
    assert len(result.data.relationships) == 2

    rels = sorted(result.data.relationships, key=lambda r: r.id)
    assert rels[0].startNodeId == "1" and rels[0].endNodeId == "2"
    assert rels[1].startNodeId == "2" and rels[1].endNodeId == "3"


def test_dedupes_nodes_and_edges_across_paths():
    a = _node(1001, "Accounts", "acct_id", {"acct_id": "A"})
    b = _node(1002, "Accounts", "acct_id", {"acct_id": "B"})
    c = _node(1003, "Accounts", "acct_id", {"acct_id": "C"})
    e1 = _edge(2001, "Transactions", {}, "from", "to", "Accounts", "Accounts")
    e2 = _edge(2002, "Transactions", {}, "from", "to", "Accounts", "Accounts")

    response = {
        "columns": ["p"],
        "data": [
            [[a, e1, b]],
            [[a, e2, c]],
        ],
    }
    result = QueryParser.parse(response, is_graph=True)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 3  # a is shared but counted once
    assert len(result.data.relationships) == 2

    node_ids = sorted(n.id for n in result.data.nodes)
    assert node_ids == ["1001", "1002", "1003"]


def test_real_world_doc_example():
    """Reproduce the exact payload shape from the user's doc example."""
    response = {
        "columns": ["p"],
        "data": [
            [[
                {
                    "id": 43980465661923,
                    "properties": {
                        "acct_id": "89CAA90C0|96B392620",
                        "bank_number": "89CAA90C0",
                        "account_number": "96B392620",
                    },
                    "metadata": {
                        "name": "Accounts",
                        "schema": [["acct_id", "text"], ["bank_number", "text"],
                                   ["account_number", "text"]],
                        "key": "acct_id",
                    },
                },
                {
                    "id": 39582419475023,
                    "properties": {
                        "from_account_id": "89CAA90C0|96B392620",
                        "to_account_id": "8AA324680|98B63A0B0",
                        "amount_paid": 49.36,
                    },
                    "metadata": {
                        "name": "Transactions",
                        "schema": [["from_account_id", "text"]],
                        "source_name": "Accounts",
                        "target_name": "Accounts",
                        "source_key": "from_account_id",
                        "target_key": "to_account_id",
                    },
                },
                {
                    "id": 43980465645172,
                    "properties": {
                        "acct_id": "8AA324680|98B63A0B0",
                    },
                    "metadata": {
                        "name": "Accounts",
                        "schema": [["acct_id", "text"]],
                        "key": "acct_id",
                    },
                },
            ]],
        ],
    }
    result = QueryParser.parse(response, is_graph=True)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 2
    assert len(result.data.relationships) == 1
    edge = result.data.relationships[0]
    assert edge.startNodeId == "43980465661923"
    assert edge.endNodeId == "43980465645172"
    assert edge.type == "Transactions"


def test_list_cell_flattened_to_comma_separated_string():
    # Array values in scalar columns are joined into a single string so the
    # UI can render them in one cell (mirrors the Spanner driver's behavior).
    response = {
        "columns": ["tags", "id"],
        "data": [
            [["a", "b", "c"], 1],
            [[1, 2, 3], 2],
            [[], 3],
        ],
    }
    result = QueryParser.parse(response, is_graph=False)
    assert result.type == "TABLE"
    assert result.data == [
        ["tags", "id"],
        ["a, b, c", 1],
        ["1, 2, 3", 2],
        ["", 3],
    ]


def test_dict_cell_without_metadata_is_table():
    # A plain dict in a cell (no metadata) is treated as a scalar in TABLE mode.
    response = {
        "columns": ["x"],
        "data": [[{"some": "dict", "without": "metadata"}]],
    }
    result = QueryParser.parse(response, is_graph=False)
    assert result.type == "TABLE"
    assert result.data == [["x"], [{"some": "dict", "without": "metadata"}]]
