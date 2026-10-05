# graphxr-database-proxy

FastAPI middleware that exposes a customer's database to GraphXR over a stable
REST contract, with a React admin UI for managing connections. One process
serves both the API and the built UI.

## Commands

Python runs through `uv` against `.venv`. Do not call `pip` directly.

- `uv run pytest` — the acceptance gate (~15s). Add `-q` for brief output.
  Offline by design; the engine-backed tests are skipped unless
  `GRAPHXR_PROXY_ENGINE_TESTS=1`.
- `uv run pytest tests/test_dialect.py -k expand` — narrow run.
- `npm run dev` — backend on :9080 (nodemon + uv) and frontend on :3002 together.
- `npm run dev:backend` / `npm run dev:frontend` — one side only.
- `npm run build` — webpack build of `frontend/` into `frontend/dist`.
- `npm start` — uvicorn on :9080 without reload; API docs at `/docs`.

The frontend is type-checked with `frontend/node_modules/.bin/tsc --noEmit -p
frontend/tsconfig.json`; `npm run build` runs webpack but not the type checker.

`black`, `isort`, `mypy` and `flake8` are declared under
`[project.optional-dependencies].dev` but are **not installed** in `.venv`, and
the checked-in code is not black-formatted. Do not reformat files you are not
otherwise changing — match the surrounding style instead.

## Layout

`src/graphxr_database_proxy/`

- `main.py` — app assembly. It sets the `OTEL_*` / `SPANNER_*` env vars **before**
  importing any Google Cloud library, and adds `PrivateNetworkAccessMiddleware`
  after `CORSMiddleware` so it wraps it. Both orderings are load-bearing; leave
  the import and middleware order alone.
- `api/` — routers. `database.py` owns the generic surface
  `/api/{database_type}/{project_name}/...` (`query`, `schema`, `graphSchema`,
  `sampleData`, `capabilities`, `expand`, `pullCategory`, `pullRelationship`,
  `search`). The others are prefixed `/api/project`, `/api/settings`, `/api/admin`.
- `drivers/` — one module per database behind `BaseDatabaseDriver` (`base.py`),
  instantiated by `DriverFactory` (`factory.py`): `spanner.py`, `bigquery.py`,
  `rocketgraph.py`, `neo4j.py`, `memgraph.py` (a subclass of `neo4j.py`),
  `kuzu.py`, `ladybug.py` (a subclass of `kuzu.py`) and `latticedb.py` (its own
  driver — LatticeDB is a separate engine, not a third Kuzu).
  Shared machinery lives in `dialect.py` — an entry that re-exports every name — whose body is
  `dialect_core.py` (the statement builders) and `dialect_backends.py` (the per-backend token
  tables, `DIALECTS`, `dialect_for`), `intents.py`
  (`GraphIntentMixin`), `graph_support.py` (per-driver capability + dialect
  wiring), and — for the bolt family — `bolt_mapping.py` (records -> `QueryData`)
  and `bolt_schema.py` (the Neo4j and Memgraph schema probes).
- `drivers/relational/` — the table-backed engines (mssql, postgresql, mysql) behind **one**
  driver. `driver.py` is the contract surface plus the connection, and its bodies are mixins:
  `driver_mapping` resolves a project's tables into an `EffectiveMapping`, `driver_query` runs
  one prepared statement, `driver_schema` answers the reads a client makes before it has a
  query. **Cypher is the internal protocol**: `dialect_core.py` emits a Cypher string for
  every backend including these, and `translate.py` → `plan.py` → `compile.py` re-read it into
  SQLAlchemy Core — so per-engine divergence lives only in `engines/*.py` behind
  `EngineSpelling`, and no shared module may branch on an engine's *name* (cast names go through
  `cast_sqlalchemy_type`; the document and shredded-column types through the abstract
  `json_document_type` / `shredded_column_type`). `mapping*.py` is the mapping document and its
  loader, `catalog.py` the paged reflection, `pool.py` the pool that outlives a request,
  `expression.py` the SQL-side expression compiler. Routes are `api/relational.py` under
  `/api/relational`, mounted in `main.py` **before** the generic
  `/api/{database_type}/{project_name}` router — that router has a path parameter exactly where
  `relational` sits, so a later mount answers `/api/relational/x/tables` with a driver lookup for
  a type that does not exist. Authority for what a mapping *is*: `doc/RELATIONAL_MAPPING.md`
  (+ its `.zh.md` twin); for the eight admin routes: `doc/RELATIONAL_API.md`.
- `drivers/embedded/` — everything the embedded engines need that a server-backed
  driver does not. A store is a file, so `store_probe.py` reads its header to find
  the engine family and storage format, `version_map.py` turns that format into a
  release (learning as it goes, into `config/engine_versions.json`),
  `runtime.py` installs that release, and `worker.py` / `pool.py` run it in its
  own process. `wheelhouse.py` is the escape hatch for an engine with no wheel for
  this platform: a wheel the user built themselves, dropped into
  `~/.graphxr-proxy/wheelhouse`, is folded into the index the installer plans
  against and installed by path in preference to PyPI. **The subprocess is not optional**: importing `kuzu` into a process
  that has already imported `ladybug` raises `generic_type: type "Database" is
  already registered!`, and LatticeDB loads its shared library from inside its own
  package, so two releases cannot share an interpreter either — the proxy can
  never hold any of them itself. The worker protocol frames its replies with a
  byte count: `readline()` gives up at its buffer limit, and every real query
  result is past it. `worker.py` has one session class per engine shape, picked by
  `session_for()`: the Kuzu family drives a `Connection`, LatticeDB opens its
  `Database` explicitly and answers dict rows.

  **The file's magic picks the engine, but only within a family.** A `kuzu`
  project holding a Ladybug store is served by Ladybug; a `latticedb` project
  holding a Kuzu store is refused, naming the type that would work. Kuzu-family
  mapping and schema live in `kuzu_mapping.py` / `kuzu_schema.py`, LatticeDB's in
  `lattice_mapping.py` / `lattice_schema.py`. `lattice_query.py` adapts a raw
  `/query` before it runs: LatticeDB has no `*`, no entity value behind a bare
  variable, and no quoted identifier at all, so `RETURN *`, `RETURN n, r, m` and
  any backtick a client emits each have to be turned into something it has — a
  label the pattern cannot carry moves into a `WHERE` predicate. It refuses
  anything it cannot read without guessing. See `doc/EMBEDDED_STORES.md`.
- `contract/` — the GraphXR graph-database contract, mirrored from
  `shared/graphdb` in the **graphxr-dev** repo. The TypeScript there is
  normative; `contract.schema.json` is vendored here and `tests/test_contract.py`
  fails if the Pydantic models drift from it. Changing a contract field means
  changing both repos.
- `models/project.py` — `DatabaseType`, `AuthType`, `DatabaseConfig`, `Project`
  and the response models.
- `services/` — JSON-file persistence into `config/`; the proxy has no database
  of its own. Also `graph_schema_cache.py`, the process-wide TTL cache behind
  `BaseDatabaseDriver.get_graph_schema_cached()` — drivers are built per request,
  so it cannot live on one; and `store_library.py`, the view over
  `config/databases` behind `/api/embedded/stores` and the Files page. Its walk is
  a generator and only the page being returned is stat-ed and header-read, so
  keep it that way — listing a library must not mean opening every file in it.
- `launcher.py` — the `graphxr-proxy` console entry point; forces UTF-8 and the
  same OTEL suppression before starting uvicorn.

`frontend/` is React 18 + antd, **all TypeScript** (`.tsx` / `.ts`). Keep new
files TypeScript.

## Adding a database driver

1. Add the enum value to `DatabaseType` in `models/project.py`, plus any new
   fields on `DatabaseConfig`.
2. Add `drivers/<name>.py` subclassing `BaseDatabaseDriver`. Every method is
   abstract and async except `get_api_info`.
3. Register it in `DriverFactory._drivers` in `drivers/factory.py`.
4. For graph intents (`expand` / `pullCategory` / `pullRelationship` / `search`),
   mix in `GraphIntentMixin`, declare a `StatementDialect` in `drivers/dialect_backends.py`
   and wire capability plus dialect in `drivers/graph_support.py` — do not re-implement
   traversal in the driver.
5. Add tests under `tests/`; `tests/test_rocketgraph_*.py` shows the shape.
   `tests/test_driver_intents.py` is parametrised over every driver, so a new one
   is covered by the conformance cases as soon as it is registered.

Keep the driver's own module free of traversal logic and of anything the client
already decides. `web/react_views/configure/graphdb/databases/<name>/` in
**graphxr-dev** is normative for a backend's capability record and dialect
tokens; port it rather than inventing a second answer.

A fourth **table-backed** engine does not follow steps 2–4: it is `engines/<name>.py`
answering every abstract `EngineSpelling` method, one `DatabaseType` value, and a
subclass of the relational driver that sets `database_type` and `engine_name`. The
abstract methods are the guard — an engine cannot be registered without answering
them, and `tests/test_relational_engine_types.py` enumerates each engine's own
answers, so a type this proxy cannot read fails there rather than in production.

Node ids have to round-trip: whatever `execute_query` puts in `Node.id` must be
what the dialect's own predicate can match again. Spanner uses `ELEMENT_ID()`,
BigQuery reads the identity out of `TO_JSON(...)`, the bolt family uses `ID()`
(not `elementId()` — that has no Memgraph equivalent and needs Neo4j 5), and
RocketGraph, which has no identity function at all, uses `<Label>:<key>`. Kuzu and
Ladybug use `<Label>:<key>` too, for a different reason: `ID(n)` exists but there
is no literal for one — `n._id` is rejected as "reserved for system usage" — so
identity cannot be matched even though it can be read. Their node tables always
have a primary key, and `table_info` says which column it is.

A relational project uses `<Label>:<key>` for a third reason: there is no stored
identity to read, so the id is built from the primary key — a key that is addressed by
**name, quoted**, because real schemas spell columns `first name` and tables
`Order Items`. That is where the two kinds of name come from: graph-side (`label`,
`type`) stays `SAFE_IDENT`, because it compiles into statement text and into that id,
while database-side (`table`, `column`, key column) is kept exactly as the engine spells
it. `doc/RELATIONAL_MAPPING.md` is the authority for the rule — do not restate or widen
it from another file.

Because those ids are built from the key rather than read off the edge, a
relationship's endpoints must be in the same result for it to be placed, which is
why their dialect returns `n, r, m` rather than `r, m`.

LatticeDB returns `n, r, m` for a different reason again, and its own module
docstrings carry the detail. Its `id(n)` *does* match, so ids are plain integers —
but it has no node or relationship value type at all (`RETURN n` answers `1`), so
the dialect projects `id/labels/properties` per variable and names each edge's
endpoints, because an edge cannot be asked for them. That is also why an
undirected expand is emitted as one statement per direction, and why nothing it
answers is a graph unless the statement projected one.

No new API route file is needed — the generic `/api/{database_type}/...` routes
pick the driver up automatically. **The client side is not automatic** — see the
next section.

## How GraphXR reaches this proxy

GraphXR opens a project served here through its **`databaseProxy`** adapter,
`web/react_views/configure/graphdb/databases/proxy/` in **graphxr-dev** — never
the native adapter of the same name. `databases/kuzu/`, `databases/neo4j/` and
the rest are *direct* connections to those databases and share nothing with us
but a dialect table; a change made there does not reach a proxy project.

The adapter takes the backend from the URL segment — `proxyBackendFromUrl()` on
`/api/<backend>/<project>` — and switches on it in two tables:

- `proxy/capabilities.ts` — `proxyCapabilitiesFor(backend)`, which must mirror
  what the driver's own `/capabilities` reports. This is a second copy of that
  record, on purpose: the client needs it before any round-trip. **`intents` is
  the one field that is not mirrored** — `supportedIntents()` reads it from the
  live document, asks once, and a 404 from an older build answers `[]`, which is
  what puts the adapter into legacy mode. The client never has to believe a
  capability claim it did not probe.
- `proxy/dialect.ts` — `proxyLegacyProfile(backend)`, the statement profile for
  **legacy mode**.

**A backend this proxy serves must have a case in both.** Neither has a guard for
an unknown backend: a missing case falls to `default`, which is the bolt family
in both tables — Cypher, `internal-id` identity, `ID(n)` predicates. That is the
best guess available and still the wrong answer for anything shaped differently.
It is what gave a Kuzu project `ID(n) IN ["<Label>:<key>", …]`: a STRING list
against an `INTERNAL_ID` column, and a binder exception with every selected node
id printed back at the user. Adding a driver here means adding it there.

Two modes, chosen by whether `GET /capabilities` lists `intents`:

- **intent mode** — the adapter POSTs `/expand`, `/pullCategory`, … and this
  proxy builds the statement. Where things should land.
- **legacy mode** — the client builds the statement from the profile above. It is
  the fallback for a proxy build with no `/capabilities`, and it is what runs
  whenever the probe fails, so its profile has to be right too.

`GET /capabilities` answers the capability **document itself**, not wrapped in
`{success, data}` like the other read routes. That is the contract; the client
unwraps three shapes to cope with it.

## Local state and secrets — never read, edit or commit

`config/databases/` (stores dropped onto a project) and `config/engines/` are
local data too, and gitignored; engine builds themselves live outside the repo,
under `~/.graphxr-proxy/engines`.

`config/projects.json` and `config/settings.json` are **live local state**
holding real connection credentials, OAuth tokens and the API key. They are
gitignored and denied in `.claude/settings.json`. The same applies to `.env`,
`config/*credentials*.json` and `config/service-account.json`. Use
`config/projects.example.json` and `.env.example` as reference instead.

## Tests

`[tool.pytest.ini_options]` sets `asyncio_mode = "auto"`, so async tests need no
decorator. `tests/conftest.py` puts `src/` on `sys.path`, so tests import
`graphxr_database_proxy.*` directly.

## Docs

- `doc/` — user-facing guides in English, each with a `.zh.md` twin
  (`DEV_GUIDE`, `USAGE`, `EMBEDDED_STORES`, publishing guides; `API_Reference`
  has no twin). Keep the pair in sync when you touch either.
- `docs/superpowers/specs/` and `docs/superpowers/plans/` — design specs and
  implementation plans, named `YYYY-MM-DD-<topic>.md`.

## Conventions

- Code, comments and git commit messages are English. Explain a change to the
  user in the chat reply, not in the commit message.
- Commit per completed, independently verifiable stage, and stage only the files
  that stage touched — never `git add -A`; other sessions may have work in flight.
- `git push`, pull requests and history rewrites only on explicit request.
- Any list or collection read must be paged at the source (cursor or
  limit/offset). Never load everything and slice in memory.
