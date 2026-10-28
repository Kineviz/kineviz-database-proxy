"""Connect a GraphXRSupportDB pg18 sample and check the running proxy over HTTP.

Credentials are read from the specified connection descriptor and sent only to
the requested proxy. The database is queried read-only; existing projects are
reused without replacing their configuration unless --mapping-file is supplied.
No credentials enter the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.relational import mapping_io
from kineviz_database_proxy.drivers.relational.pool import POOL
from kineviz_database_proxy.models.project import DatabaseConfig, Project


class Checks:
    def __init__(self, client: httpx.Client, project: str):
        self.client = client
        self.project = project
        self.base = f"/api/postgresql/{project}"
        self.passed: list[str] = []

    def request(self, method: str, path: str, body: Any = None) -> Any:
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            # The response to create includes credentials on success, so never
            # include response bodies in exceptions or output.
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
        result = response.json()
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(f"{method} {path}: {result.get('error')}")
        return result

    def query(self, statement: str) -> Any:
        return self.request("POST", self.base + "/query", {"query": statement})["data"]

    def graph(self, path: str, body: Any) -> Any:
        answer = self.request("POST", self.base + path, body)["data"]
        assert answer["type"] == "GRAPH", answer["type"]
        graph = answer["data"]
        ids = {node["id"] for node in graph["nodes"]}
        assert len(ids) == len(graph["nodes"]), "duplicate node IDs"
        assert all(
            edge["startNodeId"] in ids and edge["endNodeId"] in ids
            for edge in graph["relationships"]
        ), "relationship with missing endpoint"
        return graph

    def done(self, name: str) -> None:
        self.passed.append(name)
        print(f"PASS {name}")


async def prepare(
    descriptor: dict, mapping_text: str | None = None
) -> tuple[dict, dict, dict]:
    source = descriptor["graphxr"]
    document = mapping_io.parse(mapping_text or source["sourceMapping"])
    mapping = document.model_dump(mode="json", by_alias=True)
    config = DatabaseConfig(
        type="postgresql",
        host=source["Host"],
        port=source["Port"],
        database_id=source["Database"],
        username=source["Username"],
        password=source["Password"],
        use_tls=source.get("sourceUseTls", False),
        schema_name=document.schema_name or "public",
    )
    probe = DriverFactory.create_driver(
        Project(
            id="pg18-supportdb-check",
            name="pg18-supportdb-check",
            database_type="postgresql",
            database_config=config,
        )
    )
    try:
        server = await probe.access.probe()
        assert server.major == 18, f"expected PostgreSQL 18, got {server.major}"
        catalog = await probe.access.catalog()
        assert not catalog.warnings, catalog.warnings
        config.graph_mapping = mapping
        # Independent SQL expectations, including direction and identity, rather
        # than merely checking that each HTTP endpoint returns a 200.
        expectations = {}
        with probe.access.connection() as conn:
            for table in (
                "person",
                "company",
                "city",
                "works_at",
                "knows",
                "located_in",
            ):
                expectations[table] = conn.execute(
                    text(f'SELECT count(*) FROM public."{table}"')
                ).scalar_one()
            expectations["two_hop"] = [
                list(row)
                for row in conn.execute(
                    text(
                        "SELECT p.id, c.id, city.id FROM public.person p "
                        "JOIN public.works_at w ON w.start_key=p.id "
                        "JOIN public.company c ON c.id=w.end_key "
                        "JOIN public.located_in l ON l.start_key=c.id "
                        "JOIN public.city city ON city.id=l.end_key "
                        "WHERE city.id=1 ORDER BY p.id LIMIT 1000"
                    )
                )
            ]
            expectations["expand"] = [
                list(row)
                for row in conn.execute(
                    text(
                        "SELECT w.id, w.end_key FROM public.works_at w "
                        "WHERE w.start_key=1 ORDER BY w.id LIMIT 1000"
                    )
                )
            ]
            expectations["integer_avg"] = conn.execute(
                text("SELECT avg(age) FROM public.person")
            ).scalar_one()
            expectations["read_only"] = conn.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
        return (
            config.model_dump(mode="json"),
            mapping,
            {
                "version": server.version,
                "expectations": expectations,
            },
        )
    finally:
        POOL.dispose_project(probe.project.id)


def run_checks(check: Checks, mapping: dict, ground: dict) -> None:
    expected = ground["expectations"]
    assert expected["read_only"] == "on"
    assert {
        key: expected[key]
        for key in ("person", "company", "city", "works_at", "knows", "located_in")
    } == {
        "person": 140,
        "company": 40,
        "city": 20,
        "works_at": 124,
        "knows": 36,
        "located_in": 40,
    }
    check.done("PostgreSQL 18; read-only session; 200 nodes and 200 relationships")

    assert check.request("POST", check.base + "/test")["success"]
    check.done("connection test")
    capabilities = check.request("GET", check.base + "/capabilities")
    assert capabilities["identity"]["nodeId"] == "label-key"
    assert set(capabilities["intents"]) >= {
        "expand",
        "pullCategory",
        "pullRelationship",
    }
    check.done("capabilities")

    schema = check.request("GET", check.base + "/graphSchema")["data"]
    assert {node["name"] for node in schema["categories"]} == {
        "Person",
        "Company",
        "City",
    }
    assert {edge["name"] for edge in schema["relationships"]} == {
        "WORKS_AT",
        "KNOWS",
        "LOCATED_IN",
    }
    assert all(node["keysTypes"]["id"] == "INT64" for node in schema["categories"])
    check.done("graph schema and bigint key types")

    tables = check.request("GET", check.base + "/schema")["data"]
    assert len(tables) == 6 and tables["person"]["id"] == "BIGINT"
    assert tables["person"]["first name"] == "TEXT"
    check.done("physical schema and quoted columns")
    samples = check.request("GET", check.base + "/sampleData")["data"]
    assert set(samples) == {"Person", "Company", "City"}
    assert all(0 < len(rows) <= 10 for rows in samples.values())
    check.done("bounded samples")

    for label, table in (
        ("Person", "person"),
        ("Company", "company"),
        ("City", "city"),
    ):
        answer = check.query(f"MATCH (n:{label}) RETURN count(n) AS total")
        assert answer["data"][1][0] == expected[table], answer
    for kind, table in (
        ("WORKS_AT", "works_at"),
        ("KNOWS", "knows"),
        ("LOCATED_IN", "located_in"),
    ):
        answer = check.query(f"MATCH (a)-[r:{kind}]->(b) RETURN count(r) AS total")
        assert answer["data"][1][0] == expected[table], answer
    check.done("all six graph counts match direct SQL")

    answer = check.query(
        "MATCH (p:Person)-[:WORKS_AT]->(c:Company)-[:LOCATED_IN]->(city:City) "
        "WHERE city.id = 1 RETURN p.id, c.id, city.id ORDER BY p.id LIMIT 1000"
    )
    assert answer["data"][1:] == expected["two_hop"], answer
    check.done("two-hop join matches direct SQL")
    average = check.query("MATCH (p:Person) RETURN avg(p.age) AS average")
    assert abs(float(average["data"][1][0]) - float(expected["integer_avg"])) < 1e-9
    check.done("integer average")

    for identifier, value in ((3, "O'Brien"), (11, "北京 Wang"), (13, "Back\\slash")):
        answer = check.query(
            f"MATCH (p:Person) WHERE p.id = {identifier} RETURN p LIMIT 10"
        )
        node = answer["data"]["nodes"][0]
        assert node["id"] == f"Person:{identifier}"
        assert node["properties"]["name"] == value
    quoted = check.query(
        "MATCH (p:Person) WHERE p.`first name` = 'Ann' RETURN p LIMIT 10"
    )
    assert quoted["data"]["nodes"][0]["id"] == "Person:1"
    check.done("quoted identifiers, apostrophe, Unicode and backslash values")

    graph = check.graph(
        "/expand",
        {
            "nodeIds": ["Person:1"],
            "direction": "from",
            "relationships": ["WORKS_AT"],
            "limit": 1000,
        },
    )
    edges = graph["relationships"]
    assert {(edge["id"], edge["endNodeId"]) for edge in edges} == {
        (f"WORKS_AT:{key}", f"Company:{end}") for key, end in expected["expand"]
    }
    assert all(edge["startNodeId"] == "Person:1" for edge in edges)
    for company in sorted({edge["endNodeId"] for edge in edges}):
        reverse = check.graph(
            "/expand",
            {
                "nodeIds": [company],
                "direction": "to",
                "relationships": ["WORKS_AT"],
                "limit": 1000,
            },
        )
        assert "Person:1" in {node["id"] for node in reverse["nodes"]}
    check.done("Expand and reverse seed identity round-trip")

    category = check.graph("/pullCategory", {"category": "Person", "limit": 1000})
    assert len(category["nodes"]) == 140
    excluded = check.graph(
        "/pullCategory",
        {
            "category": "Person",
            "loadedNodeIds": ["Person:1"],
            "limit": 1000,
        },
    )
    assert len(excluded["nodes"]) == 139
    assert "Person:1" not in {node["id"] for node in excluded["nodes"]}
    check.done("pull category and loaded-node exclusion")
    for kind, total in (("WORKS_AT", 124), ("KNOWS", 36), ("LOCATED_IN", 40)):
        graph = check.graph("/pullRelationship", {"relationship": kind, "limit": 1000})
        assert len(graph["relationships"]) == total
    check.done("pull all relationship types with complete endpoints")

    discovered = check.request("POST", f"/api/relational/{check.project}/discover", {})
    assert len(discovered["model"]["nodes"]) == 3
    assert len(discovered["model"]["edges"]) == 3
    assert not discovered["warnings"], discovered["warnings"]
    page = check.request(
        "GET", f"/api/relational/{check.project}/tables?page_size=2&include=columns"
    )
    assert len(page["rows"]) == 2 and page["total"] == 6
    preview = check.request(
        "POST",
        f"/api/relational/{check.project}/preview",
        {
            "table": "works_at",
            "edge": "WORKS_AT",
            "pair": mapping["edges"][0]["endpoints"][0],
            "limit": 5,
        },
    )
    assert 0 < preview["forwards"]["count"] <= 5
    check.done("live discovery, paged tables and direction preview")

    refused = check.client.post(
        check.base + "/query", json={"query": "MATCH (p:Person) DELETE p"}
    )
    assert refused.status_code < 500 and refused.json()["success"] is False
    check.done("writes refused")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    parser.add_argument("--proxy-url", default="http://127.0.0.1:9080")
    parser.add_argument("--project", default="pg18-supportdb")
    parser.add_argument("--mapping-out", type=Path)
    parser.add_argument(
        "--mapping-file",
        type=Path,
        help="Import this JSON/YAML mapping through the admin API before testing",
    )
    args = parser.parse_args()
    descriptor = json.loads(args.connection_file.read_text(encoding="utf-8"))
    mapping_text = (
        args.mapping_file.read_text(encoding="utf-8") if args.mapping_file else None
    )
    config, mapping, ground = asyncio.run(prepare(descriptor, mapping_text))
    if args.mapping_out:
        args.mapping_out.write_text(
            json.dumps(mapping, indent=2) + "\n", encoding="utf-8"
        )
    with httpx.Client(base_url=args.proxy_url, timeout=30) as client:
        check = Checks(client, args.project)
        existing = client.get(check.base)
        if existing.status_code == 404:
            check.request(
                "POST",
                "/api/project/create",
                {
                    "name": args.project,
                    "database_type": "postgresql",
                    "database_config": config,
                },
            )
            print(f"Created project {args.project}")
        else:
            existing.raise_for_status()
            print(f"Reusing project {args.project}")
        if mapping_text is not None:
            route = f"/api/relational/{args.project}/mapping"
            saved = check.request("PUT", route, {"text": mapping_text})
            assert saved["saved"] and saved["valid"], saved.get("problems")
            for format in ("json", "yaml"):
                downloaded = client.get(route, params={"format": format})
                downloaded.raise_for_status()
                assert mapping_io.parse(downloaded.text) == mapping_io.parse(
                    mapping_text
                )
                saved = check.request("PUT", route, {"text": downloaded.text})
                assert saved["saved"] and saved["valid"], saved.get("problems")
            check.done("JSON and YAML import, download and re-import")
        run_checks(check, mapping, ground)
        print(
            json.dumps(
                {
                    "passed": len(check.passed),
                    "postgresql": ground["version"],
                    "proxy": args.proxy_url + check.base,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
