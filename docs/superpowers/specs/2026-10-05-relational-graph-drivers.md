# Relational databases as graph sources: MSSQL, PostgreSQL, MySQL

**Status:** proposed
**Date:** 2026-10-05
**Reference pattern:** `E:\projects\ibase-kineviz-bridge` (MIT), which serves an i2 iBase
database on SQL Server to Kineviz as a graph over the same proxy contract.

Three decisions taken before implementation:

- **SQLAlchemy Core is the access layer, not raw per-engine SQL text.** One seam buys
  connection pooling, identifier quoting, paging, typed bound parameters, LIKE escaping and
  — the big one — a cross-engine catalog reader (`Inspector`) for three engines. The
  bridge hand-rolls all of that in `tsql.py` + `connection.py` + `discovery.py`, and its own
  report says the seam it built (`tsql.Dialect`) is a *predicate* seam that `mssql_backend`
  reaches past in five places. Porting that structure to three engines would mean three
  copies of the parts that are hardest to get right.
- **The mapping is discovered from foreign keys by default and overridden per project.**
  In an ordinary relational schema the FK *is* the direction (child references parent), so
  the bridge's "a schema cannot tell you which way round a link goes" does not apply to the
  FK case — it applies to iBase's prefix and `_LinkEnd` links, which are out of scope here.
  Zero-config must still be reviewable, so the graph mapping is a document a person can
  rename, flip, exclude and extend.
- **The statement language is Cypher for all three engines; only the SQL compiler differs.**
  `StatementDialect` and `GraphIntentMixin` emit `MATCH … WHERE … RETURN … SKIP n LIMIT m`,
  and the relational drivers translate that to SQL. Per-engine differences (quoting,
  `TOP`/`ROW_NUMBER`/`LIMIT`, param limits) are SQLAlchemy's job at compile time, so
  `dialect.py` gains one shared relational token table rather than three.

## What is being asked

Serve `mssql`, `postgresql` and `mysql` as first-class project types of this proxy, so
GraphXR can explore a relational schema as a graph: schema and count panels, Expand,
pull-category, pull-relationship, hand-typed queries, and an admin UI to configure the
connection and review what the tables mean as nodes and links.

## Ground truth (measured, not assumed)

- **`pyodbc` has no cp314 wheel** (5.2.0 tops out at cp313) and this repo's `.venv` is
  CPython 3.14.2. `pymssql` 2.4.2, `psycopg[binary]` 3.3.6 and `PyMySQL` 1.2.3 all install
  here. So the DBAPIs are **optional extras** and every driver module must import lazily,
  exactly as `drivers/neo4j.py:28-39` does for `neo4j`.
- **SQLAlchemy 2.1.3 compiles all three dialects with no DBAPI installed.** Verified locally:
  `mssql` → `[Order]` quoting + `ROW_NUMBER() OVER (ORDER BY …)` for `limit/offset`;
  `postgresql` → `"Order"`, `LIMIT … OFFSET …`, `%(name)s::INTEGER`; `mysql` → `` `Order` ``,
  `LIMIT m, n`; and `.contains("50%", autoescape=True)` emits a correctly escaped `LIKE …
  ESCAPE '/'` on every one of them. This deletes the bridge's `tsql.quote`, `top_clause`,
  `offset_fetch`, `order_by`, `like_pattern` and `LIKE_ESCAPE` — six of the items it marks
  "not cosmetic".
- **What SQLAlchemy does *not* fix:** `avg([Order].n)` is emitted bare on mssql, so integer
  division (`avg(1,2,2)` → `1`) still needs an explicit cast from us. That plus
  id-list strategy, session settings, approximate row counts and the type vocabulary is the
  whole per-engine surface (§ "Engine surface").
- **`Inspector` gives the catalog cross-engine**: `get_table_names`, `get_multi_columns`,
  `get_multi_pk_constraint`, `get_multi_foreign_keys`, `get_sorted_table_and_fkc_names`.
  The bridge instead hand-writes `sys.*` queries (`discovery.py:85-340`); one third of that
  file is iBase-only.
- **`ibase_bridge/proxy_api.py` is not runnable today**: `element_expand.py:31` and
  `mock_backend.py:26` import `ibase_bridge.identity`, which does not exist in that tree.
  We port its **IR, plan enumeration and predicate grammar**, not its connector.
- **Identity already has a home here.** `intents.py:29` `split_label_key_id` splits on the
  *first* colon, so `<Label>:<key>` survives a key containing `:`; `_resolve_refs` (:114)
  needs `categories[label]["keys"]`, which `GraphIntentSupport` derives from the graph schema
  (`graph_support.py:236-266`). This is RocketGraph's and Kuzu's existing scheme, and the
  client already has a capability record shaped for it.
- **Intents never bind parameters** (`intents.py:149` passes `{}`), so key literals arrive in
  the statement text. Our translator converts them back to typed bound parameters, meaning
  **no client value and no client identifier is ever interpolated into SQL**.
- **`GRAPH_SCHEMA_CACHE` keys on a digest of the whole `database_config`**
  (`services/graph_schema_cache.py:59-74`), so storing the mapping inside `DatabaseConfig`
  invalidates the probe the moment the project is saved. `invalidate_project()` (:109) is
  currently called by nobody; the mapping-save endpoint should call it.
- **The client already has `case "postgresql"`** in `proxy/capabilities.ts:195` and
  `proxy/dialect.ts:148`, written for the SQL/PGQ bridge (`directions: ["from","to"]`,
  `multiHop: false`). Those values describe a different backend and must be redefined here.
- Route ordering is load-bearing: `api/main.py` includes literal-prefix routers before
  `/api/{database_type}/…`, or `/api/relational/{project}` is parsed as a database type.

## Design

### Layout (new code)

```
src/graphxr_database_proxy/drivers/relational/
  __init__.py   docstring only — no imports, no `__all__`: several lanes own sibling modules,
                and a re-export file would be a merge point every one of them has to touch
  schemas.py    NodeSchema / Endpoint / Pair / RelSchema  (the mapping IR; from backend.py,
                iBase fields dropped)
  ir.py         VertexPat / EdgePat / MatchPlan / BoundEdge / ConcretePlan  (the pattern IR;
                from backend.py + mssql_backend.py, iBase fields dropped)
  mapping.py    the override document → validated effective mapping (pure, no I/O)
  mapping_io.py YAML/JSON text <-> document: parse (UTF-8, size cap) and to_yaml (export)
  catalog.py    Inspector reads, paged by table-name page; classification; row-count estimates
  translate.py  Cypher text → MatchPlan            (from cypher_translator.py)
  predicate.py  boolean expression grammar → AST   (from predicate.py, minus to_sql)
  plan.py       MatchPlan → [ConcretePlan]          (from mssql_backend.concrete_plans)
  compile.py    ConcretePlan → SQLAlchemy Select + manifest
  expression.py predicate AST → SQLAlchemy ColumnElement, given the mapping and the engine
  convert.py    rows + manifest → GraphData; cell-value normalisation
  ids.py        <Label>:<key> mint and decode; key coercion to the column's declared type
  query.py      write guard, refusal list, auto-LIMIT, result-shape classification
                (graph / scalar / aggregate); per-engine refusals arrive through a caller-supplied
                list, because regex exists on two of the three engines and not on the third
  engine.py     EngineSpelling registry + shared connect-URL building
  engines/{mssql,postgresql,mysql}.py
  pool.py       process-wide Engine cache keyed by (project.id, config digest)
  driver.py     RelationalDriver — the whole BaseDatabaseDriver implementation
  data/mapping.example.yml   the annotated example the UI shows and a person can upload
drivers/{mssql,postgresql,mysql}.py   thin: pick the engine spelling + capability + dialect
models/graph_mapping.py               Pydantic models for the override document
api/relational.py                     mapping / discover / tables / preview / validate
frontend/src/pages/GraphMapping.tsx   review-and-edit the mapping
```

`dialect.py` (824 lines) gains only a factory plus three `DIALECTS` entries; the relational
token table is shared. `graph_support.py` gains `_relational_capabilities(name)` and
`RelationalGraphIntents` mirroring `_embedded_capabilities` (:147-179) and
`KuzuGraphIntents` (:294-296). `models/project.py` (529 lines) gains two fields and stays
under the 800-line trigger by keeping the mapping models in their own module.

### IR and pipeline (contracts, so lanes can work in parallel)

```python
@dataclass(frozen=True) class NodeSchema:  label: str; table: str; schema: str | None
    key: str; key_type: str; props: dict[str, str]              # prop -> normalised type
    show_as: str | None = None; notes: str = ""
@dataclass(frozen=True) class Endpoint:    label: str; column: str; column_type: str
@dataclass(frozen=True) class RelSchema:   type: str; table: str; schema: str | None
    key: str | None; key_type: str | None; endpoints: list[Pair]; props: dict[str, str]
    show_as: str | None = None; notes: str = ""
@dataclass(frozen=True) class Pair:        src: Endpoint; dst: Endpoint; row_estimate: int
    def swapped(self) -> Pair
@dataclass(frozen=True) class MatchPlan:   vertices: list[VertexPat]; edges: list[EdgePat]
    where: Expression | None; order: list[tuple[str,str]]; limit: int | None; skip: int
    return_items: list[str] | None; distinct: bool
```

`compile.py` builds one `MetaData` per statement, `Table()` objects from the mapping (never
reflected at query time), an `alias()` per hop, and projects every column under a
`__gx_v{i}_{k|p}{j}` / `__gx_e{i}_{k|p}{j}` label with a matching manifest, copied from the
bridge's convention because it is what makes `convert.py` position-independent. `start_var` /
`end_var` in the manifest follow **the mapping's** src/dst, not the pattern's left/right
(`test_reverse_edge_endpoint_ids_still_match_their_nodes` is the test that pins it).

### Identity, and the composite-key policy

- Node id `<Label>:<key>`, relationship id `<TYPE>:<key>`. `identity.nodeId = "label-key"`,
  `expand.predicate = "primary-key"`, matching Kuzu/RocketGraph and the client's table.
- A node label requires a **single-column key**: its PK if single, else a single-column
  NOT NULL UNIQUE constraint, else the table is excluded from the graph with a reason naming
  the rule. `NodeRef` carries one `key_prop`/`key_value` (`dialect.py:88-95`) and
  `_expand_by_key` reads `group[0].key_prop` (`dialect.py:479`), so a composite key cannot be
  expressed by the intent layer at all — this is a contract limit, not a shortcut.
- An edge prefers its own key (PK, else the widest single-column unique) so parallel links
  stay distinct; with none, the id is minted from the two endpoint keys **sorted**, so it is
  direction-stable, and the mapping doc records a warning that duplicate links will collapse.
- Keys > 2^53 and float-ish rendering are the bridge's known JavaScript traps: ids are
  carried as strings end to end and `ids.py` never routes a key through `float`.

### Discovery rules

`catalog.py` reads table names, then columns/PK/FKs **in pages of table names**
(`get_multi_*`), and never asks the DB for a full scan: row estimates come from the engine's
own statistics (`sys.partitions` / `pg_class.reltuples` / `information_schema.TABLES`).

- **link table** = exactly two FKs, not referenced by any FK, and ≥1 non-endpoint column or a
  composite/single key → `RelSchema` with `type = upper(table)`; leftover columns become edge
  properties; endpoint columns do not.
- **record table** = everything else → one `NodeSchema` per table.
- A **direct FK** on a record table also becomes an edge, `src` = referencing table,
  `dst` = referenced table, `type` = sanitised `upper(constraint_name)` when present else
  `upper(f"{child}_{fk_column}_{parent}")`, with `_2` suffixes on collision.
- Self-referencing FK → an edge whose `src.label == dst.label`.
- Caps (`max_labels` 200, `max_edges` 400, `max_props` 200) with a `truncated` warning per
  kind; a 3 000-table ERP schema is not a 3 000-label graph, and the fix is `exclude`/`include`.
- Excluded by default: views, engine-internal schemas, temp/system tables, tables whose
  single-column key is nullable.

### Override document (`models/graph_mapping.py`)

Stored in `DatabaseConfig.graph_mapping` (declared, not `options` — `DatabaseConfig` is a
closed model and silently drops unknown keys), so the cache digest changes when it changes.

```yaml
version: 1
schema: dbo                 # optional; engine default: dbo / public / the database itself
include: [Person, Employment]           # when non-empty, everything else is out
exclude: [sysdiagrams]
nodes: [{label: Person, table: Person, key: person_id, exclude_properties: [blob_col]}]
edges: [{type: WORKS_FOR, table: Employment, key: employment_id, flip: false,
         show_as: full_name,           # the property a row of this record should be labelled with
         notes: "Employment holds one line per person per org",
         endpoints: [{src: {label: Person, column: person_id},
                      dst: {label: Organization, column: organization_id}}]}]
```

`notes` and `show_as` exist because the editor regenerates the file from the model: the bridge
keeps its reasoning in YAML comments, and a comment is the first thing a save destroys. Intent
that lives in a field survives the round trip, so the reasoning a person recorded while checking
a direction is still there next time they open the page.

An override may **add an edge with no FK anywhere** (`table` + `column` stated outright),
which is how a schema that joins on a code column without a constraint still becomes a graph.
That replaces the bridge's `prefixed_fk` / `link_end` resolutions, which are not ported.
Validation is the bridge's and is kept: identifiers must be legal for the engine, an endpoint
label must be declared (a typo there is an edge type that silently never matches), a name used
for both a node and an edge is refused, and a mapping that will not load is never saved.

### Mapping files: upload, example, export

YAML is the human format — `pyyaml` is already a runtime dependency, and YAML parses JSON, so
`.yml`, `.yaml` and `.json` all take one parse path. The stored form stays the Pydantic model
inside `DatabaseConfig` (that is what the cache digest keys on); the file is a *view* of it.

- **Upload** — the browser reads the file (`FileReader`) and posts the **text**; there is no
  multipart route to keep in step. `PUT …/mapping` therefore accepts either `{"yaml": "<text>"}`
  or the JSON model, and answers with the parsed document *plus* the validation problems, each
  named by path (`edges[1].endpoints[0].dst.label: label "Ogranization" is not declared under
  nodes`). A file that will not load is reported, not saved, and never quietly truncated: cap
  1 MiB, UTF-8 required, a non-mapping top level refused.
- **The example file** ships inside the package —
  `drivers/relational/data/mapping.example.yml`, annotated with the comments a person needs
  (what `key` is for, why an edge needs its own key, how `flip` reads) — and is served by
  `GET /api/relational/mapping/example`, which requires no project. Packaging: add the glob to
  `[tool.setuptools.package-data]` (`pyproject.toml:74`) **and** a `MANIFEST.in` include, or a
  pip-installed proxy 404s where a source checkout works.
- **Drift is tested, not hoped after**: one test loads the shipped file from the installed
  package path, asserts it parses and validates clean, asserts re-serialising to YAML and
  parsing again yields a model-equal document, and asserts the endpoint returns byte-identical
  text. An example nobody parses is the stale copy the repo rules warn about. Comments do
  not survive a round trip through the model, so the file stays the authority for the *shape*
  and `mapping_io.to_yaml` the authority for what an export looks like — which is why upload,
  download and the example are one format.
- **Export / reset** — `GET …/mapping?format=yaml` returns the current override as text, and
  `PUT …/mapping` with `null` drops back to auto-discovery. Upload, download and the page's
  editor read and write one document, so there is nothing to reconcile.
- Auto-discovery is **not** re-run on upload: an override is checked against the catalog for
  *warnings* (a table not in this schema, a column that no longer exists) but those do not
  block a save, because a mapping is routinely written for a database the author cannot reach
  yet — the bridge reads the file rather than the database for the same reason.

### Visual mapping editor

`pages/GraphMapping.tsx` plus `components/mapping/*.tsx` (split so no file reaches the 800-line
trigger; `ProjectForm.tsx` is already at 1 590 and gets the smallest possible diff there). Three
tabs over **one document** — Records / Links / YAML — with Upload, Download, Reset and an Example
drawer beside them.

- **The browser never parses or emits YAML.** The editor mutates the structured document, and
  text crosses the wire only through the server: `POST …/validate` and `PUT …/mapping` take
  `{yaml: text}` *or* the JSON model and answer the parsed model; `GET …/mapping?format=yaml`
  answers text. One parser, one serializer, no second authority on the format, and no `js-yaml`
  dependency to keep in step with `pyyaml`.
- **Records tab**: one row per node label — name (rename rewrites every link that points at it),
  source table, row estimate, key chosen from that table's single-column key/unique candidates,
  property checkboxes with the physical column and its type, and **unmapped columns counted and
  expandable**, because an unmapped column is invisible in GraphXR and the usual way to learn
  that is to go looking for it in a graph a week later.
- **Links tab**: type, table, its own key, each endpoint pair with a label picker and a column
  picker fed from `GET …/tables?include=columns&page=`, `show_as` per record, flip, add a pair
  (several pairs = one polymorphic type), add a link with no FK anywhere, delete. Every link row
  carries the reasoning the discovery made ("both ends point at Account and nothing points back
  at it") so a person can disagree with a specific guess.
- **Direction is made visible, not merely editable** — the whole reason the bridge's studio
  exists. *Check direction* runs that link both ways against real data and answers one of four
  verdicts: **settled** (only this direction returns rows), **backwards** (this direction joins
  nothing — flip it in one click), **you decide** (both join, so read the row and pick the
  sentence that is true), **neither** (the table has rows but nothing joins, so the key columns
  are wrong). Rows print as `show_as` values, not ids: `1001 → 2001` cannot read as wrong while
  "Avery Chen → Northwind Logistics" can. A backwards edge returns *nothing* rather than an
  error, which is the one mistake this page exists to prevent.
- **Exclude is not silent**: unticking a record lists and drops the links that needed it, so the
  editor never produces a document that will not load (same invariant enforced in `mapping.py`,
  not only in the UI — one authority, applied on upload too).
- **YAML tab** shows `to_yaml` of the current draft live, so a person sees exactly what their
  clicks describe before saving; it is also where a pasted or uploaded file lands. Editing text
  here re-parses server-side and refreshes the two structured tabs.
- **Draft state is local, saving is one call**, and `PUT` failures map each validator path back
  onto the control that is wrong. The YAML tab and the structured tabs are two views of one
  draft object, so an unsaved edit cannot exist twice.
- Diff against auto-discovery is shown as provenance per entry (auto / renamed / flipped /
  added / excluded), because the page mixes a machine proposal and a human decision and a person
  has to be able to tell which one they are overriding.

### Engine surface (what each of `engines/*.py` must answer)

| | mssql | postgresql | mysql |
|---|---|---|---|
| DBAPI extra | `pymssql` | `psycopg[binary]` | `pymysql` |
| default port / schema | 1433 / `dbo` | 5432 / `public` | 3306 / = database |
| int/float avg + sum | `avg(CAST(x AS DECIMAL(38,10)))`, `sum(CAST(x AS DECIMAL(38,0)))` | none needed (already `numeric`) | none needed |
| big seed list | ≤2 000 inline; `OPENJSON(?)` when major ≥13 **and** `compat_level` ≥130; else chunked fan-out | `= ANY(?)` single parameter | `JSON_TABLE(?,…)` on 8.0.4+; else placeholders |
| regex | refused (no operator) | `~` | `REGEXP` |
| session guard | `SET ARITHABORT ON` + `READ UNCOMMITTED` | `SET LOCAL statement_timeout`, `READ ONLY` transaction (`READ UNCOMMITTED` is an alias for read committed there) | `MAX_EXECUTION_TIME` hint, `READ ONLY` transaction, `READ UNCOMMITTED` available |
| version probe | `SERVERPROPERTY('ProductMajorVersion')` + `sys.databases.compatibility_level` | `version()` / `server_version_num` | `version()` / `version_comment` |
| write check | `HAS_PERMS_BY_NAME(…,'DATABASE','INSERT')` | `pg_has_role(…,'pg_write_server_files')`-free route: `information_schema` + `current_setting('default_transaction_read_only')` | `SHOW GRANTS` |
| type vocabulary | int/bigint/… → `INT64`/`INT32`; varchar/nvarchar/text → `STRING`; bit → `BOOL`; float/real → `DOUBLE`; decimal/money → `DECIMAL`; date/datetime2/… → `DATE`/`TIMESTAMP`; uniqueidentifier/hierarchyid/geometry → `STRING`; varbinary/image → `BYTES` | same names, different spellings (`int2/int4/int8`, `bool`, `numeric`, `bytea`, `jsonb`, `uuid`) | same names (`tinyint/mediumint`, `enum`, `json`, blob family) |

`_NUMERIC_KEY_TYPES` (`dialect.py:32-37`) already contains those spellings, which is why the
normalised vocabulary keeps `is_numeric_key_type` working for keys.

Result cells are normalised in `convert.py` — `Decimal` → float when it fits else str;
`date`/`datetime` → ISO-8601; `bytes` → base64 with a `BYTES` property type; anything
exotic → truncated `str`. Nothing that FastAPI cannot put on the wire leaves the driver.

### Capability and dialect records

One record per engine, identical modulo `type` (the `_embedded_capabilities` precedent), and
`assert_capabilities_consistent` runs at declaration time:

```
queryLanguages ["cypher"]        identity.nodeId "label-key"
expand.predicate "primary-key"   expand.directions ["all","from","to"]   multiHop true
relationshipTypeFilter true      internalRelationships false   excludeRelationshipIds false
schema.source "proxy"            fulltextSearch {supported: false}        pull {c, r, excludeLoaded}
multiDatabase false  write false intents ["expand","pullCategory","pullRelationship"]
```

`internalRelationships: false` because a key predicate pins one end only — `_expand_by_key`
emits nothing for `onlyBetweenSelected` beyond `m <> n` (`dialect.py:488-489`), and it emits
**no** relationship-id exclusion at all (`dialect.py:469-502`), hence `excludeRelationshipIds:
false`. `directions` omits `"both"`, mirroring Kuzu: `"all"` is served by one statement whose
SQL fans out the orientations per endpoint pair.

Dialect: one shared `_relational_dialect(name)` — `predicate="primary-key"`,
`rel_id_expr=None`, `rel_type_expr=lambda var: f"type({var})"` (so hidden types are
filterable, as in Kuzu), `relationship_filter="pattern"`, `return_vars=("n","r","m")`,
`return_separator=", "`, `supports_multi_hop=True`, `supports_only_between_selected=False`,
`key_access = var.prop`, `key_literal = render_key_literal(..., quote=cypher_string)`, and
**default** `quote_identifier`/`pagination` — the statement text is our protocol, not the
engine's SQL, so all three engines share `SKIP n LIMIT m` and double-quoted identifiers.

**What the schema surface owes a `primary-key` backend.** Four facts, none of them visible from
the driver's own methods:

- `get_graph_schema()` renders the mapping document as a contract schema, and every `Category`
  must carry a non-empty `keys` — `_resolve_refs` (`intents.py:137-140`) drops a seed whose label
  has no key, so an empty `keys` turns every `/expand` into an empty graph rather than an error.
  This is why `intents.py:83` pre-loads categories for a `primary-key` backend at all.
- The driver implements `graph_key_types()` -> `{label: normalised key type}`, because
  `render_key_literal` decides quoting from it. `dialect.py:32-37` `_NUMERIC_KEY_TYPES` is
  compared lowercased, and was checked today to already contain `INT64`, `INT32`, `DOUBLE` and
  `DECIMAL` — so the shared vocabulary must keep numeric keys inside that set, or a numeric key
  gets quoted and then compared against an integer column.
- One `Relationship` per edge type, from `RelSchema.representative` (the largest pair): the
  contract allows a single `startCategory`/`endCategory` (`bolt_schema.py:302-305` states the same
  limit for bolt). The remaining pairs still answer at query time, because the plan enumerates
  every candidate pair — only the schema browser shows one.
- A `DATE` / `TIMESTAMP` / `BOOL` / `JSON` key reaches the statement as a quoted literal, which is
  correct as protocol text and wrong as SQL: the compiler binds each literal with the column's
  own type instead of pasting it, since a quoted date against a date column is a type error on
  mssql and postgresql, not a match.

### Query path and refusals

`query.py` is the only component that knows query behaviour: refuse writes before the
translator (`INSERT/UPDATE/DELETE/MERGE/CALL/CREATE/DROP/ALTER`, and any statement that is not
`MATCH`/`RETURN`/`WITH`-shaped), refuse the constructs the plan cannot answer **with a
message that names the construct** rather than returning an empty canvas —
variable-length paths, `OPTIONAL MATCH`, `WITH`/`UNWIND`, subqueries, `HAVING`, `DISTINCT`
inside an aggregate, mssql regex, composite keys in a pattern, and a pattern that names a
label or type the mapping does not have. Auto-LIMIT when the statement has none (default
1 000, capped by `options.max_limit`), and trim-on-output when a fan-out made each branch
apply the limit itself; paging **across** branches stays best-effort and says so in
`QueryData.summary`. `count`/`sum`/`avg`/`min`/`max` with grouping, `DISTINCT`, `ORDER BY`,
`SKIP`/`LIMIT` and multi-hop are supported, as they are in the bridge.

Three things `query.py` deliberately does **not** own, so the driver and the compiler must:

- A probe or `CALL`-shaped statement has to be recognised **before** `prepare`, which refuses
  `CALL` by design. Counting and sampling are the driver's `get_graph_schema` / `sample_data`
  path, not a query shape.
- `Lit` in the AST is a typed Python value and the dialect emits text with no parameters, so the
  compiler decides inline-versus-bind per engine — and per the note above, binds a non-string key
  with the column's own type rather than as text.
- `toLower`/`toUpper` folding is lossy: `n.name = toLower('X')` and `n.name = 'x'` parse to the
  same AST. Nothing downstream can recover a case-insensitive comparison, so an engine whose
  collation is case-sensitive has to be read from `EngineSpelling`, not from the AST.

Branch fan-out converges the total only in the driver: `prepare` bounds one statement, and a
multi-category or multi-direction expand runs several.

### Endpoints

`api/relational.py`, prefix `/api/relational`, admin-token like `/api/embedded`, included
**before** the generic router in `main.py`:

| | |
|---|---|
| `GET  /mapping/example` | the shipped annotated example YAML — no project needed |
| `GET  /{project}/mapping` | effective mapping + provenance (auto ∘ override) + warnings; `?format=yaml` returns the override as text (download) |
| `PUT  /{project}/mapping` | save an override from JSON **or** `{"yaml": text}` (`null` resets to auto); invalidates `GRAPH_SCHEMA_CACHE` |
| `POST /{project}/discover` | catalog → proposed mapping with the reasoning per guess and the caps hit, not stored |
| `GET  /{project}/tables` | paged (`?page=&page_size=`) table list with column counts and row estimates; `?include=columns&table=` for one table's columns, which is what the pickers read |
| `POST /{project}/preview` | ≤100 real rows for one edge pair, **both directions in one call**, answered as settled / backwards / you-decide / neither |
| `POST /{project}/validate` | parse (text **or** model) and check without saving; every problem carries its path |

The generic `/api/{database_type}/{project}/…` surface needs nothing new, and `/schema` —
which every graph driver answers with "use /graphSchema" (`neo4j.py:376-386`) — becomes the
real table-and-column probe here, bounded by the mapping rather than the whole database.

### Frontend

- `ProjectForm.tsx`: uncomment `mysql` (:820), add `mssql`, uncomment `postgresql`; add
  `username_password` to the auth-options gate (:837-852, which today renders **zero** options
  for these types); default ports in `handleDatabaseTypeChange` (:282-345); a config card
  reusing the neo4j block's shape (:1245-1364) with host / port / database (`database_id`) /
  schema (`schema_name`) / user / password / `use_tls` / read-only login hint; and a
  `handleSubmit` branch (:155-280) or the submitted config carries nothing. A relational
  create also takes an **optional mapping upload**, so a person who already has a reviewed
  file never has to auto-discover and re-type it; leaving it empty means auto-discovery.
- `pages/GraphMapping.tsx` is the visual editor of §"Visual mapping editor", built on
  `StoreLibrary.tsx`'s shape (Table + pagination + admin-token calls + an antd `Upload` that
  reads the file client-side — the drop-target pattern already exists there). Upload accepts
  `.yml,.yaml,.json` up to 1 MiB, Download and the Example drawer are the same served text, and
  Reset returns the project to pure auto-discovery.
- one `<Route>` in `App.tsx:117-126`, one menu item in `AppHeader.tsx:21-42`, methods on
  `ProjectService`. `types/project.ts` and `ProjectManagement.tsx:167-188` already cover
  postgresql/mysql and need an `mssql` colour case.

### graphxr-dev (same round, separate commits in that repo)

`proxy/capabilities.ts`: replace the `postgresql` case's SQL/PGQ values with the record above
and add `mssql` / `mysql` sharing it. `proxy/dialect.ts`: `proxyLegacyProfile` gains the same
three names on a profile modelled on `rocketGraphProfile` (which is already `primary-key` +
label-key identity), because `default` there is the bolt family — Cypher with `ID(n)` — and
that is the wrong answer for a table. Neither table has a guard for an unknown backend, so
this half is not optional if the feature is to be usable.

## Verification

- **Offline, the real gate.** `uv run pytest` must stay green and stay offline. New suites:
  `test_relational_mapping` (validation, every refusal by name), `test_relational_catalog`
  (fake Inspector), `test_relational_translate` + `test_relational_predicate` (grammar,
  precedence, the reject list), `test_relational_plan` (branch enumeration, caps, label
  inference), `test_relational_compile` (**asserts the emitted SQL per engine** — quoting,
  `ROW_NUMBER` vs `LIMIT/OFFSET`, `ANY` vs `OPENJSON` vs `JSON_TABLE`, the avg/sum casts —
  using `sa.dialects.*.dialect()` with no DBAPI installed), `test_relational_convert`,
  `test_relational_ids` (round-trip, 2^53, colon-in-key), `test_relational_query` (refusals
  refuse rather than empty), `test_relational_endpoints` (`TestClient` +
  `dependency_overrides[get_project_service]`, the habit from `test_embedded_endpoints.py:106`).
- **The shipped example is a test fixture, not a comment.** `test_relational_mapping_io`
  loads `drivers/relational/data/mapping.example.yml` from the package path, parses and
  validates it, checks YAML → model → YAML → model is model-equal, and checks
  `GET /api/relational/mapping/example` returns it byte-identical; plus the upload failures a
  person actually hits — a label typo, a missing `key` on an edge, tabs in the indentation, a
  2 MiB file, a JSON body, and a file whose top level is a list — each answered by the path
  that is wrong, not by "invalid mapping".
- **A real SQL round trip with no server**: `tests/test_relational_pipeline.py` runs the whole
  pipeline against **in-memory SQLite** (stdlib `sqlite3`, so no DBAPI is installed at all),
  the way the bridge uses `MockBackend` but through genuine SQL — including the expand round
  trip, which is the one thing a compile-only test cannot prove.
- **The editor's promises are backend invariants, not UI behaviour.** The rules the page depends
  on live in `mapping.py` and `catalog.py` and are tested there, so an uploaded file obeys them
  exactly as a clicked one does: excluding a record drops the links that needed it; a rename
  rewrites every endpoint that points at the old label; every validator message carries a path a
  form control can be found by; and `POST …/preview` answers the four direction verdicts from
  real joins, which the SQLite fixture can produce offline. There is no editor logic that exists
  only in TypeScript — if it mattered, it would be a test the backend fails.
- **Engine-backed, gated** by `GRAPHXR_PROXY_ENGINE_TESTS=1` +
  `GRAPHXR_PROXY_RELATIONAL_{MSSQL,POSTGRESQL,MYSQL}_Dsn` env, skipped otherwise, following
  the existing convention. CI stays offline.
- **Frontend**: `frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` (webpack
  does not type-check) plus `npm run build`.
- **Manual**: `npm run dev`, point at a throwaway container (AdventureWorks-style schema or
  the Northwind fixture), connect from GraphXR, confirm the schema panel, then flip a link's
  direction and confirm the preview rows change.

## Phases and lanes

Shared worktree, disjoint owned paths, lane stops when done, CTO commits per pathspec.

| Lane | Owned paths | Delivers |
|---|---|---|
| A | `drivers/relational/{ir,mapping,mapping_io,catalog}.py`, `drivers/relational/data/mapping.example.yml`, `models/graph_mapping.py`, matching tests | mapping document + the invariants the editor relies on (path-named problems, exclude-cascade, provenance), file round trip, discovery |
| B | `drivers/relational/{translate,predicate,query}.py`, matching tests | Cypher front-end |
| C | `drivers/relational/{plan,compile,expression,convert,ids,engine}.py`, `engines/*.py`, matching tests | SQL generation per engine |
| D | `drivers/relational/{pool,driver}.py`, `drivers/{mssql,postgresql,mysql}.py`, `drivers/{dialect,graph_support,factory}.py`, `api/relational.py`, `models/project.py`, `main.py`, `pyproject.toml`, `MANIFEST.in`, `requirements.txt`, matching tests | wiring + endpoints + packaging of the example file |
| E | `frontend/src/pages/GraphMapping.tsx`, `frontend/src/components/mapping/*.tsx`, `frontend/src/services/projectService.ts`, `App.tsx`, `AppHeader.tsx`, `types/project.ts`, and the relational branches of `ProjectForm.tsx` | the editor, the project form, the upload/download/example UI |
| F | graphxr-dev `…/proxy/{capabilities,dialect}.ts` | client tables |

C and D consume A's and B's contracts as written above, so they start immediately; D's
registration cannot be committed before A/B/C exist. E needs D's routes to exist but not its
drivers, so it starts against a stubbed `projectService` and lands in Phase 2. Phase gates:
**1** = A+B+C+D with `uv run pytest` green and one commit per lane-stage; **2** = E green under
`tsc --noEmit` and `npm run build`; **3** = F in the other repo. Docs in the same round: `CLAUDE.md` (adding a
driver section, the identity paragraph, `drivers/relational/` in the layout), the
`doc/DEV_GUIDE` + `.zh.md` and `doc/USAGE` + `.zh.md` pairs, `doc/API_Reference.md`, the
`doc/RELATIONAL_MAPPING.md` + `.zh.md` pair that carries the mapping-file prose, and the new
optional extras in the publishing docs. The example file is **linked** from that doc and served
to the UI, never copied into either — one fact, one authority.

## Not in this round

MongoDB (still an unimplemented enum value); iBase `prefixed_fk` / `link_end` resolutions;
full-text search (denied by the capability, as for Kuzu and LatticeDB); writing back to the
database; Oracle/MariaDB/SQLite as project types (the seam admits them, nobody has measured
them); variable-length paths. Out of the editor too: undo/redo history, a per-entry reason
beyond the one `notes` field, writing the file back out to the server's disk (it lives in the
project, and Download is how it becomes a file), and a mapping that spans several projects.
