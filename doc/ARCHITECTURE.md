# Architecture and driver extension

[English](ARCHITECTURE.md) | [中文](ARCHITECTURE.zh.md)

Read these notes when changing the corresponding subsystem. Everyday commands
and development conventions live in [AGENTS.md](../AGENTS.md).

## Application and contracts

`src/graphxr_database_proxy/main.py` assembles FastAPI and serves `frontend/dist`
when present, otherwise packaged static assets. Preserve these orderings:

- Set `OTEL_*` / `SPANNER_*` environment variables before Google Cloud imports.
- Add `PrivateNetworkAccessMiddleware` after `CORSMiddleware` so it wraps CORS.
- Mount `/api/relational` before `/api/{database_type}/{project_name}`; otherwise
  the generic route interprets `relational` as a database type.

`contract/contract.schema.json` mirrors `shared/graphdb` in **graphxr-dev**, where
the TypeScript contract is authoritative. `tests/test_contract.py` checks the
Pydantic models against it. Contract field changes need coordinated changes in
both repositories. `/capabilities` returns the document directly; other read
routes generally wrap results in `{success, data}`.

`services/` persists JSON configuration. Drivers are created per request, so the
graph-schema TTL cache lives in `services/graph_schema_cache.py`, not on a driver.
`services/store_library.py` pages the store walk and probes only the returned
page; preserve that behavior.

## Shared graph behavior and identity

Drivers implement `BaseDatabaseDriver` and are registered in `DriverFactory`.
Graph intents use `GraphIntentMixin`, `StatementDialect` and `graph_support.py`;
keep traversal out of individual drivers. `dialect.py` re-exports names from
`dialect_core.py` (builders) and `dialect_backends.py` (backend tokens/registry).
The bolt family shares `bolt_mapping.py` and `bolt_schema.py`.

Node IDs emitted by a driver must match its subsequent identity predicates:

| Backend | Identity rule |
| --- | --- |
| Spanner | `ELEMENT_ID()` |
| BigQuery | Identity read from `TO_JSON(...)` |
| Neo4j / Memgraph | `ID()`, preserving shared compatibility |
| RocketGraph | `<Label>:<key>`; no identity function |
| Kuzu / Ladybug | `<Label>:<key>` from the table primary key; internal IDs cannot be matched as literals |
| Relational | `<Label>:<key>` from the named, quoted primary-key column |
| LatticeDB | Integer `id(n)` |

Key-based graph results must include both endpoints to place an edge; dialects
return `n, r, m`. LatticeDB instead projects IDs, labels and properties explicitly
because it has no entity value type, and expands each direction separately.

## Relational databases

`drivers/relational/driver.py` shares its mapping, query and schema implementations
through `driver_mapping.py`, `driver_query.py` and `driver_schema.py`. Cypher is
the internal protocol: `translate.py` -> `plan.py` -> `compile.py` turns graph
statements into SQLAlchemy Core.

Backend differences belong in `engines/*.py` behind `EngineSpelling`. Shared
modules must not branch on engine names. Cast and document/shredded-column types
use `cast_sqlalchemy_type`, `json_document_type` and `shredded_column_type`.
Mapping files/loaders, paged catalog reflection and reusable pools are separate
modules. Graph labels/types obey `SAFE_IDENT`; database table/column names retain
their exact spelling and are quoted. [RELATIONAL_MAPPING](RELATIONAL_MAPPING.md)
is authoritative for these semantics; [RELATIONAL_API](RELATIONAL_API.md)
documents the admin routes.

## Embedded databases

`drivers/embedded/` probes store headers, resolves versions, installs runtimes
and runs them through `worker.py` / `pool.py`. Keep native engines in separate
processes: Kuzu and Ladybug conflict when imported together, and multiple
LatticeDB releases cannot share an interpreter. Worker replies use byte-length
framing; line-based reads cannot carry large query results.

Store magic selects an engine only within its family. Kuzu projects may open
Ladybug stores; LatticeDB projects must refuse Kuzu-family stores. LatticeDB is
a separate engine with its own mapping/schema/session and query adapter:
`lattice_query.py` rewrites unsupported projections/identifiers and refuses
statements it cannot interpret safely. Kuzu/Ladybug share their own mapping and
schema helpers. User-built wheels can be supplied through the wheelhouse;
version resolution, runtime paths and platform details live in
[EMBEDDED_STORES](EMBEDDED_STORES.md).

## Adding a backend

1. Add `DatabaseType` and any required `DatabaseConfig` fields in `models/project.py`.
2. Implement `BaseDatabaseDriver` and register it in `drivers/factory.py`.
3. Wire graph intents, dialect and capabilities through the shared modules above.
4. Add backend tests and check `tests/test_driver_intents.py` conformance coverage.
5. Update UI configuration and the **graphxr-dev** proxy adapter described below.

A new relational engine instead implements every abstract `EngineSpelling` method
in `relational/engines/`, registers its spelling, and subclasses the existing
relational driver with `database_type` and `engine_name`. Check
`tests/test_relational_engine_types.py`. Generic database API routes pick up the
registered driver; client support still needs explicit wiring.

## GraphXR client integration

GraphXR reaches this service through `web/react_views/configure/graphdb/databases/proxy/`
in **graphxr-dev**. Its native database adapters are separate direct connections.
For every proxy backend, update both `proxy/capabilities.ts` and `proxy/dialect.ts`.
Their unknown-backend fallback uses bolt identity/dialect and may emit invalid
statements for other engines.

The client mirrors static capability fields, but probes `intents` from the live
`/capabilities` response. Intent mode POSTs `/expand`, `/pullCategory`, etc. so the
proxy builds statements. Missing/failed probes (including older proxies' 404s)
fall back to client-built statements using the legacy profile. Keep both modes
correct. The client's native capability/dialect definitions are the reference
when porting backend support; changes in the other repository must be reported
separately if that checkout is unavailable.
