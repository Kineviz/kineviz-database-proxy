# Relational admin API

`/api/relational` is the editing surface behind the Graph Mapping page: the routes that
list a customer's tables, propose a mapping, check one, save one, and run one link both
ways against real rows. It serves the three relational backends — `mssql`, `postgresql`
and `mysql` — and refuses every other project type by name.

Three documents already own parts of this story, and none of them owns these routes:

| | Authority for |
|---|---|
| [`doc/RELATIONAL_MAPPING.md`](RELATIONAL_MAPPING.md) | the mapping document: its fields, what discovery proposes, what it refuses |
| [`doc/API_Reference.md`](API_Reference.md) | the generic graph surface `/api/{database_type}/{project_name}/…` |
| [the spec](../docs/superpowers/specs/2026-10-05-relational-graph-drivers.md) | the pipeline: IR, compiler, capability and dialect records |

This document is the authority for the eight admin routes and their payloads. Where it
overlaps the others it links instead of restating.

## The rules every route here follows

**Prefix, and why the mount order matters.** The router sits at `/api/relational` and is
included in `main.py` **before** the generic `/api/{database_type}/{project_name}` router.
That is load-bearing, not tidiness: the generic pattern has a path parameter exactly where
`relational` sits, so a later mount would read `/api/relational/mapped/tables` as "the
`tables` endpoint of project `mapped` of database type `relational`" and answer it with a
driver lookup for a type that does not exist.

**Auth.** Every route here depends on `verify_admin_token`, the same admin session token
`/api/embedded` uses: an `X-Admin-Token` header, issued by the admin login, and required
only when `ADMIN_PASSWORD` is set.

**A relational project is also a graph project.** Everything below edits the mapping; the
generic graph routes consume it, and that is how GraphXR reaches the database at all — see
[The same project on the graph surface](#the-same-project-on-the-graph-surface).

## Bodies here are not wrapped

The graph surface answers `{"success", "data", "error"}` for its read routes, and
`GET /capabilities` answers the capability document itself, unwrapped, as a standing
exception. `/api/relational` is a third thing: **every route answers its own shape, with no
envelope at all.** There is no `success` field to check on a 200 — a 200 means the route
did its read, and the body says what it found.

Deliberately. A page editing a draft needs the *fields* (`model`, `text`, `problems`,
`key_candidates`), not a flag it has to unwrap first, and a mapping that is legal to save and
still not loadable is exactly the case where a boolean hides the answer — so whether a
mapping loads is `valid` plus `problems`, two facts a `success` flag would collapse into one.

## A refusal is an answer, and it carries a code

Failures here are named, not thrown as a 500. A request that can be fixed gets its problem
described with the path of the control that needs fixing:

| Code | When | `detail` looks like |
|---|---|---|
| `400` | the project is not `mssql` / `postgresql` / `mysql`; the engine's DBAPI is not installed; an upstream failure with nothing to point at | a sentence |
| `404` | no project of that name; no table of that name; no saved mapping when `?format=yaml`; the shipped example unreadable | a sentence |
| `422` | a mapping problem, a draft connection that does not validate, a request that names no column | `[{"path": …, "message": …}]` |
| `500` | a `DriverFactory` registration pointing a relational type at a non-relational driver | a sentence |

The `422` items are always `{"path", "message"}` pairs — the shape a rejected mapping upload
uses too — because a person fixing a draft should not have to learn two error formats from
one page. A missing DBAPI keeps the engine's own install sentence, which names the extra
*and* the package. The two refusals a reader meets first, verbatim:

```
project northwind is a neo4j project; /api/relational serves mssql, postgresql, mysql
project northwind does not exist
```

The one `500` here is a bug in the build, not an answer for a user.

## The eight routes

| | | |
|---|---|---|
| `GET` | `/api/relational/mapping/example` | the shipped annotated example, as text — no project |
| `GET` | `/api/relational/{project}/mapping` | the saved override, as a document or as a file |
| `PUT` | `/api/relational/{project}/mapping` | save it, or `null` to take it away |
| `POST` | `/api/relational/{project}/validate` | parse and load a draft; store nothing |
| `POST` | `/api/relational/{project}/discover` | read the live catalog and propose a mapping |
| `GET` | `/api/relational/{project}/tables` | one paged window of the schema, for the pickers |
| `POST` | `/api/relational/{project}/tables` | the same read against an unsaved connection |
| `POST` | `/api/relational/{project}/preview` | one link, both directions, real rows |

## GET /api/relational/mapping/example

The example file byte for byte, so a page can show it without paraphrasing it:
`{"name": "mapping.example.yml", "text": "<the file, verbatim>"}`.

Served from inside the package (`drivers/relational/data/mapping.example.yml`), not from a
fixture path a deployment might not have. The reason is authority: a page that retypes the
example teaches a different format from the one the parser accepts. Unreadable at runtime —
an odd thing, but a packaged data file can be stripped by an install — is a `404`,
`the shipped mapping example is not readable: …`.

## GET /api/relational/{project}/mapping

The override this project has saved. **It reaches no database at all**: what a person saved
is answerable without asking the customer's server, and a page that cannot load because a
host is unreachable is worse than one that says the draft is not there yet. `POST /discover`
is the route that reads the live schema.

On success:

| Field | |
|---|---|
| `project`, `id`, `backend` | what you asked for, echoed so a page can tell a stale answer from a fresh one |
| `source` | `"override"` when a mapping is saved, `"none"` when it never was |
| `valid` | the saved document *loads*, not merely parses |
| `problems` | `[{"path", "message"}]`, empty when it loads |
| `warnings` | mismatch with the live catalog, a keyless edge, a cap hit — reported, never a blocker |
| `provenance` | per entry, where it came from |
| `model` | the document as an object — what a form holds, or `null` |
| `text` | the same document as YAML — what a file tab holds, or `null` |

`model` and `text` come from one parse, so the form and the text tab are two views of one
answer and can never disagree.

`?format=yaml` returns the file instead of the document: `text/yaml`, with
`Content-Disposition: attachment; filename="<project>.mapping.yml"`. With no saved mapping
that is a `404`, not an empty file, and it names the way to get one:

```
project northwind has no saved mapping; POST /api/relational/northwind/discover and save
what it proposes
```

`source` is about storage, not about who wrote the file. `"draft"` never appears here — it
is what `/discover` answers, for a proposal nobody has accepted.

## PUT /api/relational/{project}/mapping

Saves an override, or removes it. Send the mapping as the object **or** as the text —
`{"model": …}` or `{"yaml": …}` — and **a `null` body removes the override**, returning the
project to auto-discovery. That is the honest inverse of saving one, and the only way a
person gets the machine's original answer back. For the document's own fields, see
[RELATIONAL_MAPPING.md](RELATIONAL_MAPPING.md).

Both halves take one road: the object is dumped to text and parsed rather than validated
straight into the model, so the form path and the upload path share one size cap, one set of
path-named problems and one rule about what an unknown key means. A body with neither is
refused rather than read as "empty mapping", which would silently delete someone's file:

```
file: send the mapping as 'model' or as 'yaml', not neither
```

The answer is the `GET` shape plus two flags: `saved: true`, and `removed: true` when the
body was `null`.

**A mapping that will not load is still saved.** `valid: false` and the list of `problems`
come back with the `200`, because a record with no `key` is a legal thing to save and
discovery fills it in — refusing the save would make a draft impossible to keep while still
being wrong. What an unloadable mapping costs is the graph surface, which refuses it there
and says so. Each problem's meaning is
[Validation is per control](RELATIONAL_MAPPING.md#validation-is-per-control-and-it-is-the-same-list-either-way).

**Saving invalidates the derived cache by itself.** The route drops this project's cached
graph schema and disposes its pooled engine. The cache is the one that matters for
correctness, and it needs no help to be right: the schema cache key digests the whole
connection config, and the mapping is stored *in* that config (`DatabaseConfig.graph_mapping`),
so a saved mapping changes the key and the next probe sees your version. The pool is cleaned
because a connection open against a database whose mapping just changed has no reason to
stay open.

## POST /api/relational/{project}/validate

Checks a draft without saving it and without touching the customer's database. Same body as
the `PUT`. On success: `valid` (no load problems), `problems` (`[{"path", "message"}]`),
`warnings`, `nodes` and `edges` (how many entries each list has), and `text` — the proxy's
own rendering of the draft you sent, which is what lets the YAML tab show what your clicks
describe before you save.

Two sources of problems, merged into one list: shape problems come from the parse with the
control's path, semantic ones from the loader. A file that parses and still cannot load is
therefore reported as the paths that need fixing, not as a mapping that will fail later on
someone's canvas.

## POST /api/relational/{project}/discover

Reads the live catalog and proposes a mapping. Body optional: `{"schema": …}` to walk a
schema the project has not saved, `{"connection": …}` for a project that does not exist yet
(see the draft note under `POST /tables`). On success:

```json
{"project": "northwind", "source": "draft", "model": {…}, "warnings": ["…"]}
```

**`source: "draft"` means machine-proposed and not yet accepted by a person**, and this
route **stores nothing** — a proposal nobody has looked at is not a mapping. Saving is the
separate, explicit `PUT …/mapping`. `warnings` carries the caps and the skips, so "three
hundred tables became forty labels" has a reason on the screen instead of being a silent
loss. What the proposal contains, and every rule that shaped it, is
[What auto-discovery proposes](RELATIONAL_MAPPING.md#what-auto-discovery-proposes).

## GET /api/relational/{project}/tables

The picker's read: a page of table names, and on request one table's columns.

| Query | |
|---|---|
| `page` | 1-based, minimum 1 |
| `page_size` | names on the page — default 50, ceiling 500 |
| `search` | names containing this, case-insensitively |
| `schema` | this schema instead of the project's |
| `include=columns` | columns instead of a count |
| `table` | one table, in full |

A page of names:

```json
{"rows": [{"table": "Order", "schema": "dbo", "columns": 12, "row_estimate": 1180}],
 "total": 47, "page": 1, "page_size": 50}
```

`columns` is a **count** here unless `include=columns`. `row_estimate` comes from the
engine's own statistics and is never a scan. Whether a table *can* be a node is not repeated
as a flag: it is `key_candidates`, and a table with none is refused by the save with the
catalog's reason, not by a checkbox.

**Paging is on names, and a page of names is the unit of reflection.** Asking for twenty
tables costs twenty tables' worth of catalog reads, never the library's: the reflection is
handed exactly the names on the page, so listing a schema never means opening every table in
it. `?search=` filters *names* before anything is reflected, so a
search stays one page too; a picker that pulled the whole library down to filter it in the
browser is the mistake this rule exists to stop. `total` counts the names that match, which
is one catalog query an inspector cannot offset — the only whole read left in the walk.
`include=columns&table=` counts *columns* instead, because that read is the column picker
paging a wide table.

One table, in full (`?include=columns&table=Person`):

```json
{"table": "Person", "schema": "dbo", "row_estimate": 3400,
 "columns": [{"name": "person_id", "type": "int", "nullable": false,
              "is_primary_key": true, "is_unique": false}],
 "key_candidates": ["person_id"], "total": 214, "page": 1, "page_size": 200}
```

`columns` is paged; **`key_candidates` is not** — it is the whole table's answer. It is the
catalog's judgement over every column it read (a single-column primary key, else a
single-column `NOT NULL` unique), and a picker offered only the first 200 columns of a
400-column table would hide the key sitting past them: a candidate in column 250 must not
vanish from a dropdown. Each column row keeps the engine's own spelling (`nvarchar(100)`) —
this is the relational view, not the graph.

A `?table=` name the schema does not have is a `404`, saying what was asked and what it is
not:

```
OrderLine is not a table in dbo — a view is not, and neither is anything the engine keeps
for itself
```

A name that is on the page but that the catalog did not answer for comes back with
`columns: null` and `row_estimate: null` rather than being dropped, so a page keeps its
shape when one read fails.

## POST /api/relational/{project}/tables

The same read, one method over, against a connection that has **not been saved**. Body:
`{"project": <name for the answer>, "connection": <a database config, as the form holds
it>, "schema": …, "page": …, "page_size": …, "include": …, "table": …, "search": …}`, and
the answer is identical to the `GET` one — one walk shared by both methods, so a draft
cannot drift from the saved project it may become.

The reason it exists: the picker has to work *before* a project is saved. A person choosing
which tables are their graph is looking at a database they have not committed to the proxy,
and a page that only shows tables for a project that already exists makes them save a
connection they may still abandon. A draft gets an engine id of its own, keyed by a digest
of the whole config, so the pool never mistakes it for the saved project of the same name —
and two different drafts cannot share an engine either, because a different password is a
different digest. Nothing is stored here.

**Credentials travel in the body and never in a query string, because a URL ends up in an
access log and a body does not.**

A `connection` that does not validate is a `422` naming the field, in the same
`{"path", "message"}` shape as a mapping problem: a `port` of `"not-a-port"` answers with a
`path` starting `port`.

## POST /api/relational/{project}/preview

One link's join, run **both ways** against real data, in one call. This is the route that
catches the mistake no schema can answer.

Body: `{"edge": <type name, for the answer only>, "table": <the table holding the rows>,
"pair": <index, or {"src": …, "dst": …}>, "src": …, "dst": …, "limit": 20}`. `limit` is
rows *per direction*, capped at 100 — past that a preview is an export, and an export is a
query the person should have to write. The two endpoints are what the join uses; `edge` only
names the answer, because a draft being edited has no saved type yet.

On success:

```json
{"table": "Employment", "edge": "WORKS_FOR", "verdict": "backwards", "pair": 0,
 "forwards": {"rows": [{"from_label": "Avery Chen", "to_label": "Northwind Logistics",
                         "from": 1001, "to": 2001}], "count": 1},
 "backwards": {"rows": [], "count": 0}}
```

`pair` is echoed only when the caller sent an index, and it is the editor's address for the
pair, not part of the join. Each direction is its own field rather than one list, so a reader
never has to remember which end of an array it drew its picture from. `from_label` /
`to_label` are the values a person can *read* and `from` / `to` the keys behind them:
`1001 → 2001` cannot be seen to be wrong, `Avery Chen → Northwind Logistics` can — and the
labels are named for the *reading*, so the flipped direction comes back with its arrow the
other way round.

`verdict` is a closed set of four words:

| | |
|---|---|
| `settled` | only the declared direction joins anything — the link means what the mapping says |
| `backwards` | the declared direction joins nothing and the swapped one does |
| `you-decide` | both join rows, so the database cannot settle it and a person can |
| `neither` | the table has rows and nothing joins them — one of the two key columns is wrong |

**An empty result is never a success signal here.** Both directions empty is `neither`,
never `settled`: reported as settled it would put a full canvas of nothing on the screen and
let a person conclude their link is fine. A link whose ends are swapped answers nothing
rather than raising an error — it does not fail, the edge simply never appears — and that is
precisely why this route exists: the rows are the only thing that can say which way round
the key columns point.

`you-decide` is spelled with a hyphen because that is the word the spec uses. The page shows
it as **You decide** — it normalises `[_-]` to a space — which is a rendering of the same
token, not a third spelling.

A pair that names no column is refused, with the path of the control left empty, rather than
run against a guess:

```
pair: send the two ends as 'src' and 'dst', or the pair itself as {'src': …, 'dst': …} — a
direction check joins on two named columns and a pair number alone does not carry them
```

An endpoint whose label the mapping does not know is the same shape, pointed at that
endpoint, and it names the label rather than answering empty:

```
Employment.dst: Ogranization is not a record type in this mapping: give the pair a label
the mapping has, or name its table and key
```

The two directions are two statements, and the check reaches the customer's database —
unlike `GET /mapping` and `/validate`, which never do.

## Two constraints that decide a project's shape

Both are contract limits rather than this proxy's preference, and a reader who does not know
them files a wrong-mapping bug. The full reasoning is in
[The one idea](RELATIONAL_MAPPING.md#the-one-idea).

**A node key is one column, always.** The graph contract carries one key pair per seed node,
so a table keyed by `(order_id, line_no)` cannot be expanded at all. The proxy **refuses** a
composite key rather than quietly using the first column, which would return
plausible-looking wrong rows. Discovery then never proposes that table as a node, and the
reason names the columns.

**An edge's endpoints must be resolvable from `<Label>:<key>`.** A node id here is built
from the key, not read off a row pointer, so a relationship can only be placed if both ends
are in the same result — which is why a statement for these projects returns `n, r, m`, and
why a seed comes back as a predicate on its own key column, that column written quoted
(`n.`first name` IN [...]`): this proxy parses the statement it emits, so the dialect and the
client's own profile have to spell a name the same way or a key column holding a space cannot
be expanded at all. An edge naming an endpoint the mapping does not declare is refused at
load, and `/preview` names it instead of answering empty. How a name in the mapping document
itself is checked — strict on the graph side, the database's own spelling on the other — is
[the mapping guide](RELATIONAL_MAPPING.md).

## The same project on the graph surface

A project of type `mssql`, `postgresql` or `mysql` is also served by the generic
`/api/{database_type}/{project_name}/…` routes, and **that** is what GraphXR's
`databaseProxy` adapter calls — never the native adapter of the same name. The mapping saved
above is what those routes read.

| Route | What it does for a relational project |
|---|---|
| `/capabilities` | the capability document, unwrapped; lists `intents`, so the client stays in intent mode |
| `/graphSchema` | the mapping as the contract's schema, from the cache keyed on the config digest |
| `/schema` | a real table-and-column probe, bounded by the mapping and paged by name page — where the Neo4j and Memgraph drivers must refuse |
| `/query` | Cypher-shaped text translated to per-engine SQL; writes and unanswerable constructs refused by name |
| `/expand`, `/pullCategory`, `/pullRelationship`, `/search` | graph intents over the same mapping, seeds re-selected by primary key |
| `/sampleData` | a bounded look at each label, `LIMIT` at the source, only the mapped columns |

The contract, the wrapping and the request bodies of these belong to
[`doc/API_Reference.md`](API_Reference.md) and are not restated here. What those routes
refuse, and why each refusal names its construct, is
[Query path and refusals](../docs/superpowers/specs/2026-10-05-relational-graph-drivers.md#query-path-and-refusals).
The client side is not automatic: a backend served here needs a case in both
`proxy/capabilities.ts` and `proxy/dialect.ts` in **graphxr-dev**, whose `default` is the
bolt family and is the wrong answer for anything shaped differently.

## Where the facts live

| Question | File |
|---|---|
| The routes, their bodies and their refusals | `api/relational.py` |
| The direction check and the verdict set | `drivers/relational/reads.py` |
| Paging, the pool, the config digest | `drivers/relational/pool.py` |
| Route-level behaviour | `tests/test_relational_endpoints.py` |

Where the mapping's own rules live is the guide's table, not a second copy here:
[Where the rules live](RELATIONAL_MAPPING.md#where-the-rules-live).
