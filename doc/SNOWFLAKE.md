# Snowflake

[English](SNOWFLAKE.md) | [中文](SNOWFLAKE.zh.md)

Snowflake uses the shared relational driver: table discovery, graph queries,
Expand, category/relationship pulls, direction previews and the visual Graph
Mapping editor. Both JSON v2 and YAML mappings are supported; their semantics
are documented in [RELATIONAL_MAPPING](RELATIONAL_MAPPING.md).

## Install and connect

```sh
uv sync --extra dev --extra snowflake
```

For packaged installations, use `pip install "graphxr-database-proxy[snowflake]"`.
The `relational` extra includes Snowflake alongside SQL Server, PostgreSQL and
MySQL. Snowflake's SQLAlchemy dialect currently requires SQLAlchemy 2.0; this
extra constrains the version below 2.1.

Choose **Snowflake** when creating a project. Enter:

| Field | Example | Meaning |
| --- | --- | --- |
| Account identifier | `myorg-myaccount` | Account identifier; a full `*.snowflakecomputing.com` hostname is also accepted. Omit URL schemes and paths. |
| Database | `ANALYTICS` | Initial database |
| Schema | `PUBLIC` | Default schema; mapping entries may override it |
| Warehouse | `GRAPHXR_WH` | Warehouse used for graph reads |
| Role | `GRAPHXR_READER` | Optional; empty uses the login's default role |
| Username / Password | your login | Password authentication |

Connections always use HTTPS. The account is stored in `host`, the database in
`database_id`, the schema in `schema_name`, and warehouse/role in
`options.warehouse` / `options.role`. These are the same values used by **Test
Connection**, **Browse tables** and the saved project. Additional connector
authentication may be selected with `options.authenticator`; the UI exposes
username/password authentication. Key-pair and OAuth token configuration are
not exposed by this integration.

Use a role with `USAGE` on the warehouse, database and schema and `SELECT` on
mapped tables. The proxy's graph translator rejects writes. Snowflake has no
read-only transaction mode, so database privileges must also enforce read-only
access. `options.connect_timeout` defaults to 15 seconds and
`options.query_timeout` to 120 seconds; the latter also sets the server's
`STATEMENT_TIMEOUT_IN_SECONDS`.

The GraphXR proxy API URL is `/api/snowflake/<project-name>`. Mapping management
uses the existing `/api/relational/<project-name>/...` routes in
[RELATIONAL_API](RELATIONAL_API.md).

## Mapping names and identities

Use the physical names returned by **Browse tables**. Snowflake stores ordinary
unquoted identifiers in uppercase. A quoted lowercase or mixed-case identifier
is a different name. The proxy preserves those names in its catalog and quotes
schemas, tables and columns in generated SQL. For example, graph label `Person`
can map to physical table `PERSON` with key `ID` and graph property `name` mapped
to column `NAME`.

[The example mapping](../examples/snowflake.graphxr-mapping.json) uses `PERSON`,
`COMPANY` and `WORKS_AT` in `PUBLIC`. Adjust the names to match your tables, then
import it as JSON; the YAML tab can edit or export the same mapping.

Standard Snowflake tables do not enforce `PRIMARY KEY`, `UNIQUE` or `FOREIGN
KEY` constraints. Discovery reports a warning: verify keys are unique and
non-null and that relationship values join to the intended nodes. Tables with
no declared keys/foreign keys need an explicit mapping. A composite key still
needs one suitable unique column for graph identity.

`NUMBER(p,0)` / `DECIMAL(p,0)` keys retain their full precision, including 38-digit
identifiers. Fractional numbers remain decimals. Timestamp variants are
reported as timestamps. `VARIANT`, `OBJECT` and `ARRAY` properties can be read,
but scalar predicates on those columns require path semantics that this
pipeline does not implement; use scalar source columns for filtering and keys.

The table picker filters and pages `INFORMATION_SCHEMA.TABLES` in Snowflake.
Column reads are limited to the requested tables. Row estimates use catalog
`ROW_COUNT`, avoiding scans of the source tables. Large key selections use
bounded query chunks.

## Validation

```sh
npm test -- tests/test_snowflake.py tests/test_relational_capability_record.py -q
npm run typecheck
npm run build
```

The tests run offline, including compilation with the installed Snowflake
dialect. Without the optional extra, dialect-specific tests are skipped.
Live validation needs an authorized Snowflake account and warehouse; PostgreSQL
sample tests do not establish connectivity to Snowflake.

GraphXR clients should recognize `snowflake` as a relational proxy backend,
use label/key identity, and negotiate the advertised graph intents. Older
clients with static backend lists need Snowflake added to both their proxy
capability and dialect tables; see [the integration notes](ARCHITECTURE.md#graphxr-client-integration).

Official references: [Snowflake SQLAlchemy](https://docs.snowflake.com/en/developer-guide/python-connector/sqlalchemy),
[Python connector connection parameters](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api),
[constraint behavior](https://docs.snowflake.com/en/sql-reference/constraints-overview).
