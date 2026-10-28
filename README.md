# Kineviz Database Proxy

> This project replaces `graphxr-database-proxy`. Install
> [kineviz-database-proxy](https://pypi.org/project/kineviz-database-proxy/)
> and use the [new repository](https://github.com/Kineviz/kineviz-database-proxy).

[English](README.md) | [中文](README.zh.md)

FastAPI middleware that connects [GraphXR](https://www.kineviz.com/graphxr) to
databases through a REST API, with a React admin UI for connections and settings.

Supports Spanner Graph, BigQuery, RocketGraph, Neo4j, Memgraph, Kuzu, Ladybug,
LatticeDB, SQL Server, PostgreSQL, MySQL and Snowflake. Optional admin-password and API-key
authentication protect management and database access.

## Quick start

```bash
pip install "kineviz-database-proxy[ui]"
kineviz-proxy --ui
```

The Python package and source directory are named `kineviz_database_proxy`.
Update existing Python imports to use this name. The `graphxr-proxy` command,
`GRAPHXR_PROXY_*` environment variables and existing storage paths remain
supported for compatibility.

Open http://localhost:9080/admin, create a project and copy its API URL into
GraphXR's **Database Proxy** connection. For example:
`http://localhost:9080/api/spanner/Test`.

For SQL drivers, install the corresponding extra: `[mssql]`, `[postgresql]`,
`[mysql]`, `[snowflake]` or `[relational]` for all four. See the
[user guide](doc/USAGE.md) for connection and authentication setup.
Snowflake connection and mapping details are in the [Snowflake guide](doc/SNOWFLAKE.md).

## Develop from source

Requires Python >= 3.9, Node.js >= 22 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Kineviz/kineviz-database-proxy.git
cd kineviz-database-proxy
uv sync --extra dev
npm install
npm run install:frontend
npm run dev
```

The API runs at http://localhost:9080; open the frontend URL printed by webpack
(the port is selected automatically unless `PORT` is set). API docs are at
http://localhost:9080/docs.

Claude Code, Codex, Cursor, GitHub Copilot and Gemini CLI share
[AGENTS.md](AGENTS.md). See the [development guide](doc/DEV_GUIDE.md) for tool
entry points and verification commands.

## Docker

```bash
docker run -d -p 9080:9080 --name kineviz-database-proxy -v graphxr-proxy-config:/app/config kineviz/kineviz-database-proxy:latest
```

Open http://localhost:9080/admin. The named volume preserves project configuration.

## Documentation

- [Usage and authentication](doc/USAGE.md) · [Python examples](examples/README.md)
- [Development](doc/DEV_GUIDE.md) · [Architecture](doc/ARCHITECTURE.md)
- [API reference](doc/API_Reference.md) · [Relational admin API](doc/RELATIONAL_API.md)
- [Relational mapping](doc/RELATIONAL_MAPPING.md) · [Embedded stores](doc/EMBEDDED_STORES.md)
- [Docker publishing](doc/DOCKER_PUBLISHING.md) · [PyPI publishing](doc/PYPI_PUBLISHING.md)

[MIT license](LICENSE) · [Issues](https://github.com/Kineviz/kineviz-database-proxy/issues)
· support@kineviz.com
