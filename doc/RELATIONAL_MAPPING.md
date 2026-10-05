# SQL Server, PostgreSQL and MySQL as a graph

You have a database of tables and columns, and you want GraphXR to show it as dots and
lines. The proxy reads your catalog, proposes a reading of it, and lets you correct that
proposal in one document. This is about the document, and about what the proxy will not
guess for you.

## The one idea

| Your schema | GraphXR sees |
|---|---|
| A record table (`Person`) | one node **label**, one node per row |
| The column that identifies a row (`person_id`) | the node's **id**: `Person:1001` |
| A link table (`Employment`) or a foreign key | one relationship **type**, e.g. `WORKS_FOR` |
| The other columns | properties of the node, or of the relationship |

The id is **built from the key**, not read off the row pointer. That is what lets an
expand hand an id back and find the same row again: it re-selects it with the very
predicate that produced the id. The form is `<Label>:<key>` — first colon is the split, so
a key containing colons survives.

**One column, always.** A label needs exactly one key column: its primary key if that is
single-column, else a single-column `NOT NULL` unique, else the table is not a node. This
is not a shortcut — the graph contract carries one key pair per seed node, and a table
keyed by `(order_id, line_no)` cannot be expanded at all. Narrowing it to one of the two
columns is worse than refusing: it would return plausible-looking wrong rows. See
[`drivers/relational/mapping.py`](../src/graphxr_database_proxy/drivers/relational/mapping.py).
Everything below that mentions "single column" is this rule.

**A link's endpoint column is a column on the link table.** Each end names a label and the
column *on the edge's own table* that holds that end's key; the node's side comes from its
`key`. So the join is `<edge>.<column> = <node>.<key>`: `Employment.person_id` joins
`Person.person_id`, and `OrderLine.region_code` joins `Region.region_code` even though the
names differ.

## What auto-discovery proposes

Leave the mapping out entirely and the proxy reads the foreign keys and gets a first draft.
It asks for table names, then columns, primary keys and foreign keys a page of names at a
time — a 3 000-table ERP schema is never reflected in one call. Row counts come from the
engine's own statistics, so they are estimates and the page prints them as `~1,200,000`.

| What it finds | What it proposes |
|---|---|
| Exactly two foreign keys, both single-column, and nothing points back at the table | an edge, `type = upper(table)`, one endpoint pair per foreign key |
| A foreign key on a record table | an edge whose **src is the referencing table** — that direction is a fact about the schema, not a guess |
| A foreign key pointing back at its own table | an edge with one label on both ends |
| Any other table | a node label, keyed by its single-column primary key, else its widest single-column `NOT NULL` unique |
| A name that is not a legal identifier, or is already taken | a sanitized name, `_2` on collision |

Each guess arrives with its reasoning written into `notes`, because a draft you cannot
disagree with is not reviewable, it is just accepted:

```
EMPLOYMENT  <- Employment  notes: link table: exactly two foreign keys (person_id -> Person,
                                organization_id -> Organization) and nothing points back at it
FK_ACCOUNT_PARENT <- Account notes: Account.parent_account_id points back at Account itself,
                                      so the edge has one label on both ends
OrderItem   <- OrderItem   notes: not a node: its primary key is ['order_id', 'line_no'] and
                                the contract matches one column per key
```

## What it refuses to guess

These refusals are the difference between a graph and a canvas that looks like "no data".
Each is reported with its reason, so a table that is missing is missing on purpose.

| Situation | What happens | What to do |
|---|---|---|
| Primary key is two or more columns | not a node; the reason names the columns | map it yourself and name the one column that really identifies a row |
| A unique column exists but is nullable | not used as a key — a null key cannot be a dot | make it `NOT NULL`; stating it anyway is allowed, with a warning that expand will fail on it |
| Foreign key spans two columns | no edge is drawn; the constraint is named | declare the edge yourself with the one column that really joins |
| A junction table whose foreign keys are composite | read as a record, not as a link | declare it as an edge and name its endpoint columns |
| A link points at a table that is not in the graph | no edge is drawn, and it says which end | pull that table in, or drop the link |
| Views, engine-internal schemas, temp and system tables | never considered, counted in one warning | they are not your data; if a view *is*, materialise it or map the base tables |

The caps are the same refusal at the other end: **200 labels**, **400 edges**, **200
properties** per record ([`catalog.py`](../src/graphxr_database_proxy/drivers/relational/catalog.py)).
Past a cap the biggest records are kept — a table nobody counted ranks above one counted at
zero, because nobody proved it small — and the rest are dropped with `not mapped: the labels
cap is 200` in their `notes`, while the page warns "Discovery stopped at a cap". A 3 000-table
schema is not a 3 000-label graph: the panel becomes unusable and the first expand fans out
across all of it. Name what you want with `include`, or what you do not with `exclude`.

## The mapping file

One file per project. Everything in it is optional: what you write is what the proxy stops
guessing. The annotated copy the proxy ships is
[`drivers/relational/data/mapping.example.yml`](../src/graphxr_database_proxy/drivers/relational/data/mapping.example.yml)
— the **Example** button on the page shows it, and it parses and loads clean, so copy from it.

At the top level:

| Field | Changes |
|---|---|
| `version` | document format; only `1` exists |
| `schema` | which schema every table below lives in, unless an entry names its own. Defaults: `dbo` (SQL Server), `public` (PostgreSQL), the database itself (MySQL) |
| `include` | when non-empty, **only** the named records are in the graph |
| `exclude` | the named records are out — the short form of an entry with `include: false` |
| `nodes` / `edges` | the records and links, in the order you wrote them |
| `tuning` | reserved for per-project query-layer overrides; the example names the keys, and each one is documented where it is read |

A name in `include` / `exclude` matches a label or a table, case-insensitively, whichever
one you happen to remember. When a schema was read to check against, a name that matches
nothing is warned about once rather than silently ignored.

A node entry:

| Field | Changes |
|---|---|
| `label` | the name GraphXR sees. Renaming it rewrites every link that points at it |
| `table` | the physical table; defaults to the label |
| `schema` | this table's schema, when it is not the document's |
| `key` | the one column that identifies a row; left out, discovery's candidate is used |
| `properties` | a **whitelist** — see below |
| `exclude_properties` | the other way to narrow it; what a blob column is for |
| `include: false` | keeps the entry as a decision without putting it in the graph |
| `show_as` | which property a row should be *called* |
| `notes` | why this record is a node, and why its key is the one it is |

An edge entry: `type`, `table`, `schema`, `include`, `properties`,
`exclude_properties`, `show_as` and `notes` mean the same as above, for a link. What is
only an edge's:

| Field | Changes |
|---|---|
| `endpoints` | every `(src, dst)` this type connects — one pair per shape you declare |
| `key` | the edge's **own** key: its primary key, else its widest single-column unique |
| `flip` | read the whole link the other way round: swaps *every* endpoint pair, not one |

Each endpoint is `{label, column}`: a node label plus the column **on the edge table**
(see [The one idea](#the-one-idea)), and an optional `column_type` carried so the file
needs no live catalog.

### The three things people get wrong

**`properties` is a whitelist.** Name columns and anything not on the list is invisible in
GraphXR — which is usually how a person learns an unmapped column existed, a week later,
looking for it in a graph. Leave it out and you get whatever discovery found for the table;
write it and a column added to the table later stays out until it is named. A bare list of
names works too; those columns are then typed `STRING`. The key and the endpoint columns are
never properties, whatever you write.

**An edge wants its own key.** `WORKS_FOR` on `Employment` without `employment_id` puts two
people working at one organization on different dates onto *one* link: with no key of its
own the id is minted from the two endpoint keys sorted, which stays stable when you flip the
direction but collapses duplicate links between the same pair. Discovery fills the key in
when the table has one and warns when it does not; a hand-written file with no catalog to
ask is *refused* until you name it, because silence there would be a graph with links
missing from it.

**`flip` is a sentence, not a correction.** Discovery follows the foreign key, which is a
real direction but not always the one the room uses. `WORKS_FOR` as declared answers "who
works at where"; flipped it answers "where employs whom". Both are valid graphs. Which one
is *yours* is a question real rows answer, not a schema — see
[Check the direction](#check-the-direction-before-you-believe-the-graph).

### Why the reasoning lives in fields

`notes` and `show_as` exist because the editor regenerates the file from the document, and a
YAML comment is the first thing a save destroys. The example file is rich in comments for a
person's first read; **an exported mapping has none**. Intent that lives in a field is still
there next time you open the page.

## Upload and edit are the same document

The Graph Mapping page (in the navigation bar) lists your projects; only SQL Server,
PostgreSQL and MySQL ones are selectable, and the rest are greyed with the reason — a graph
database already names its own nodes, so there is nothing to infer from tables. Picking a
project puts it in the URL, so a link to it lands on the same review.

Three tabs — **Records**, **Links**, **YAML** — are views of one draft, and Save is one call
for the whole thing. Beside them: **Upload mapping**, **Download**, **Re-discover**,
**Example**, **Reset to auto-discovery**, **Reload**, **Discard**, **Save mapping**.

The important part is that a file and a click are the same thing. **The browser never parses
or emits YAML**: an upload is read as text in the page and handed to the proxy, which is the
only program that turns text into a mapping or a mapping back into text. So an uploaded file
is bound by exactly the rules a clicked edit is — the same exclusions, the same cascades, the
same refusals. `.yml`, `.yaml` and `.json` all take one parse path, because YAML reads JSON.
One parser, so there is no second authority on the format to drift.

What that means in practice:

- A file the proxy will not load is **reported, not applied**. You get the list of problems
  and your current mapping is unchanged; nothing is ever quietly truncated.
- A file that reads cleanly lands in the **draft**, not in storage — Save still has to happen,
  and the card says `unsaved` until it does. Leaving the page with a draft asks you first.
- **Download** writes the *stored* mapping, because the proxy renders what it holds; with an
  unsaved draft the page offers to save first.
- **Re-discover** replaces the draft with what the catalog proposes now. Nothing is stored
  until you save.
- A **mapping file can also be attached when you create the project**. A file that will not
  load then costs a warning after the project exists, never the project itself.
- Saving changes what GraphXR reads: the schema cache is keyed on the mapping, so the next
  schema probe sees your version.

The **YAML** tab shows the proxy's own rendering of the draft as you click, so you see what
your clicks describe before you save; pasting text there and applying it re-parses
server-side and refreshes the other two tabs, and text with problems leaves them unchanged.
Excluding a record drops the links that needed it, naming which record caused it, rather than
refusing the file — the same cascade `mapping.py` applies to an upload, so the editor's promise
and the loader's rule are one rule.

A file is capped at **1 MiB** and must be UTF-8. Past that, or with a top level that is not a
mapping, or empty, the proxy refuses the whole file and says which.

## Validation is per control, and it is the same list either way

Every problem names the control that is wrong, so an upload that fails lights up a field
rather than the page. Each snippet below is a real file, and each message is what loading it
printed:

```yaml
version: 1
exclue:
  - sysdiagrams
```

```
exclue: not a field of the mapping document (a typo is refused, not ignored)
```

Unknown keys are refused rather than dropped, because `exclue:` loading as "nothing excluded"
reads back as a mapping that silently does nothing. A syntax error names the line instead:
`line 3: indentation uses a tab; YAML indents with spaces`.

```yaml
nodes:
  - label: OrderItem
    table: OrderItem
    key: [order_id, line_no]
```

```
nodes[0].key: node key 'order_id, line_no' is composite and the graph contract carries one key
pair per seed node (NodeRef.key_prop) and _expand_by_key reads group[0].key_prop, so a table
keyed by more than one column cannot be expanded at all; declare one of ['order_id', 'line_no']
as a surrogate or single column
```

A key written as a list is kept readable so the refusal can name both columns instead of
answering "not a string" about a file that is perfectly clear.

The last one is a file whose `nodes` list declares `Person` and nothing else:

```yaml
edges:
  - type: WORKS_FOR
    table: Employment
    key: employment_id
    endpoints:
      - src: {label: Person, column: person_id}
        dst: {label: Ogranization, column: organization_id}
```

```
edges[0].endpoints[0].dst.label: label 'Ogranization' is not declared under nodes; a typo here
is an edge type that silently never matches. Declared labels: Person
```

Also refused: a name that is not letters, digits and underscore (the proxy quotes reserved
words for you at the SQL boundary, but a label with a space has no place in a generated
statement); a duplicate label or type; one table mapped as two labels, which cannot round-trip
an id; an edge with no endpoint pair, which is a relationship that can never be drawn; and one
name used for both a node and an edge, because `<Employment>:<key>` would then mean two things.

**Mismatch with the live database is a warning, never a blocker.** A table that is not in this
schema, a column that no longer exists, a name in `include` that matches nothing — each is
said once and the save still happens, because a mapping is routinely written for a database
its author cannot reach yet.

## Check the direction before you believe the graph

A **backwards edge returns nothing rather than an error.** It does not fail; the link simply
never appears, and the mapping looks fine. That is the one mistake this page exists to
prevent, and no schema can answer it — a foreign key points one way, and the sentence you
mean may point the other.

Each endpoint pair on the Links tab has **Check direction**, which runs that link *both ways*
against your real rows and gives one of four verdicts:

| Verdict | Meaning |
|---|---|
| **Settled** | only this direction joins anything. The link means what the mapping says it means |
| **Backwards** | this direction joins nothing, the other does — one click flips it |
| **You decide** | both join rows, so the database cannot settle it; read the rows and pick the sentence that is true |
| **Neither** | the table has rows and nothing joins them, so at least one of the two key columns is the wrong one |

The rows are printed as `show_as` values, not ids, because `1001 → 2001` cannot read as wrong
while `Avery Chen → Northwind Logistics` can; hover for the ids, since a label that reads
right can still be joined on the wrong column. "No rows" is never reported as a clean result,
and a check that could not run is not a verdict — no direction has been ruled out.

The **Records** tab expands each entry's columns: how many are properties, which are not in
the graph (counted and listed, for the same reason as the whitelist above), whether the entry
carries a whitelist or takes all columns, and the key candidates the proxy reported for that
table.

Every row carries a provenance tag — `auto`, `renamed`, `flipped`, `added`, `excluded` —
because the page mixes a machine's proposal and your decisions, and you have to be able to
tell which one you are overriding.

## When the graph looks wrong

| Symptom | Usually means | Change |
|---|---|---|
| A label is missing | the table has no single-column key, or a cap dropped it, or a view | read its `notes`; declare a `key`, or use `include` |
| A link is missing | a composite foreign key, or an end outside the graph, or `include: false` on a record it needed | state the edge yourself; pull the record back in |
| An edge joins nothing | it runs backwards, or an endpoint column is the wrong one | **Check direction**, then flip or fix the column |
| A column does not show in GraphXR | `properties` is a whitelist and the column is not on it | name it, or clear the whitelist |
| Two links between the same pair are one | the edge has no key of its own | give it its own `key` |
| Too many links, or a table nobody asked for | discovery proposed every record table it could key | `exclude`, or `include` with a short list |
| The first expand is slow or huge | a polymorphic type fanning out over every pair | keep the pairs you mean, or exclude the type |
| Nothing at all on the canvas | the project cannot open its database, so no catalog was read | fix the connection first — a mapping is a reading of a catalog that has to be reachable |

## Creating the project

Host, port, database, schema, username and password, plus TLS. The defaults are the engine's
own: `1433`, `5432`, `3306`, and `dbo` / `public` / the database itself for the schema — MySQL
leaves the schema empty on purpose, because there the schema *is* the database. **A read-only
login is enough**: the proxy only ever reads. Anything an engine takes that the form has not
been asked for goes in the connection's `options`, which are passed straight to the driver.

## Where the rules live

| Question | File |
|---|---|
| The document's fields | `models/graph_mapping.py` |
| Parsing, the 1 MiB cap, the export | `drivers/relational/mapping_io.py` |
| What is valid, and why not | `drivers/relational/mapping.py` |
| What discovery reads and refuses, the caps | `drivers/relational/catalog.py`, `reflect.py` |
| The IR everything downstream is written against | `drivers/relational/schemas.py` |
| The shipped example | `drivers/relational/data/mapping.example.yml` |
| The editor | `frontend/src/pages/GraphMapping.tsx`, `frontend/src/components/mapping/` |

No route or payload is documented here: [`doc/API_Reference.md`](API_Reference.md) is where
the mapping endpoints belong once they are settled, and the admin UI is what this page
describes.
