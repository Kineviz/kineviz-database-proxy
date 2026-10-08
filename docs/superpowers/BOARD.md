# Progress board

Rolling state only: who is where (collision) and how to pick a lane up
(recoverability). Durable rules and rulings live in `.claude/RULES.md`; what
became true of the product lives in the docs and in `git log`.

## In flight

| Who | Block | Owned paths | Next step | Last commit |
|---|---|---|---|---|
| lane-C1 (redo) | `_require_bindable_key` in `compile.py:639-661` raises `UnbindableKey` with no remedy, and the sentence blurs two facts a person would act on differently: the schema named **no type at all** vs the schema named a real type this engine has no binding for (`geometry` is the measured example; `xml` is NOT — PostgreSQL binds it as Text) | `src/graphxr_database_proxy/drivers/relational/compile.py`, `tests/test_relational_compile.py`, new `tests/test_relational_key_refusal.py` | split the message into the two cases, name the remedy that exists (key on a nameable column, or exclude the record), drop the false one (declaring a type for a key — there is no `keyType` field); pin at the route; survival rule: `git add` the new test file immediately. See `.claude/RULES.md:17` | be40901 |
| lane-M5 (redo) | the key-marker route: `mapping_index.py:58-63` writes an **edge endpoint's recorded type** into the same per-table column map, and `driver_mapping.py:149-158` `_key_unnamed()` does not consult `names_a_type`, so a node keyed on a column an edge also names can reach `key_type = "NULL"` with the bounded catalog read skipped. Consequence: refused for **every** statement, not just filters | `src/graphxr_database_proxy/drivers/relational/{mapping,mapping_props,mapping_index,driver_mapping}.py`, `src/graphxr_database_proxy/models/graph_mapping.py`, the eight `tests/test_relational_mapping*.py` and `tests/test_relational_{names,property_types}.py`, new `tests/test_relational_key_marker.py` | reproduce or refute the route end to end; if reachable, decide whether aligning `_key_unnamed` with `names_a_type` is behaviour-neutral (previous run measured yes for the cases tried) — pin either way; the `keyType` field is a contract change, do NOT build it; report doc drift; survival rule: `git add` the new test file | be40901 |
| lane-D7 (audit) | docs drift: three doc lies this round (vocabulary names, `xml` example, save-refusal copy) — find the next one | READ-ONLY on `doc/RELATIONAL_MAPPING.md`, `doc/RELATIONAL_MAPPING.zh.md`, `doc/RELATIONAL_API.md`, `doc/USAGE.md`, `doc/USAGE.zh.md`; edits nothing | walk every factual claim in the five files, verify each against the code at file:line, report drift only; do not write | be40901 |

## Recent

- 2026-10-08 — `571777b` (lane-R8): **a declared array refuses, a thrown probe is reported, and
  the dialect spells the type.** `type_name` now takes the reader's own dialect; each engine's
  `column_type` routes an array-shaped declaration to `array_binding` (refuses by name); a probe
  that throws adds to `Gaps` so `catalog.warnings` carries the sentence rather than silently
  dropping the page. Verified at HEAD `c51ac58` in an isolated tree: 1822 passed, 8 skipped.
  Brief premise corrected by the CTO before land (the silent element-text bug is not PG-only —
  at HEAD `TEXT[]` binds as TEXT on all three engines, and mssql/mysql ignored `declared.array`
  entirely; R8's branch fixes the two as well as PG).
- 2026-10-08 — `be40901` (CTO): **the API reference now says what `/validate` and `/discover`
  answer and why.** `/validate` gains `provenance` between `warnings` and `nodes` (the checked
  draft's own tags, not the stored document's); `/discover` keeps answering none, and the
  paragraph says why — every entry is discovery's own, a tag per row could only say one word,
  and `source: "draft"` already says it about the whole document.
- 2026-10-06 — `c51ac58` (lane-D6): **the mapping twins now tell a person which type they are
  allowed to state.** A property keeps its declared type only where the catalog names none, while
  a key takes the schema answer and is refused for every statement when that answer has no
  binding. Includes a troubleshooting row separating a graph-side vocabulary name from a
  genuinely unbindable column. (Both sentences slightly stale: the "blank or STRING" sentence
  is true only until M5 decides on the marker; the `geometry` example is right at HEAD. C1 and
  M5 will report anything that needs to change.)
- 2026-10-06 — `87c38ae` (lane-T7): **placement of a same-label link, and of a link the page
  cuts in half.** 18 new cases in `test_relational_placement.py` — a same-label expand whose
  two ends differ, a neighbour identified by property (not just id), a self-referential
  `TreeNode.parent_id`, a same-label `pullRelationship`, a legacy single-type expand, and a
  page trimmed too small to carry both ends of a link. Mutation proof: shared-key clamp → 18
  failed; the trimmed-page case needs `_combine` to keep only links whose ends survived.
- 2026-10-06 — `5b40c4b` (lane-F6): **the check answers the tags of the document it just
  checked, and the editor installs them.** `/validate` now answers `provenance`; `/discover`
  deliberately still answers none, and both decisions are in the route docstrings. Frontend
  install paths (`saved`, `replace` from paste) carry the tags through. Red-proof: removing
  the backend line → 5 of the 6 new cases fail. No screen promises a save refusal any more.
- 2026-10-06 — `fd56103` (CTO): **the legacy two-hop fails when an edge lands on the same
  vertex twice.** `assert len(ids) == len(set(ids))` could never catch a merged chain — identical
  ids collapse in `convert`. The claims that bite are `len(set(ids)) > 1` and no-self-loop;
  I proved it by re-clamping the fixture (`else edge_keys, kind, 0, name`) in an isolated tree:
  3 failed, each naming `Person:1001 -[MANAGER]-> Person:1001`.
- 2026-10-06 — `0bfa833` (lane-T6): **the graph stand-in answers each vertex with its own
  key**, asks for one key per entity the statement joins, and `GET /api/relational/mapping/example`
  has 6 route-level cases. Two lessons captured in rules: an untracked lane file is not safe
  until committed (the disappearance pattern), and a gate that misses an indirect importer is
  not a gate.
- 2026-10-06 — `bb0e72d` (lane-M4) + `f080f19` (lane-F5): **a type the schema cannot name now
  keeps the one the person declared**, with a warning saying that this declared name is what
  a filter binds as — and only where the catalog names no type. And the install path now
  clears the diagnostics that describe the document that left the screen.
- 2026-10-06 — `5b16734` + `abc72eb`: **the editor compares a note against the proposal that
  actually arrived**, and no screen promises a save refusal any more. `MappingAuto` is gone with
  `MappingEnvelope.auto`; the baseline is keyed `nodes:Person` / `edges:WORKS_FOR`.
- 2026-10-06 — `d7fdf00` (lane-D5): **both usage guides describe a relational project**, in
  the same order, every control name read out of the code. Two unverifiable claims were
  dropped (a connection probe that reports response time; "Currently supports `spanner`").

## Next up

- **Tentatively stale (c51ac58):** `doc/RELATIONAL_MAPPING.md:175-187` and `.zh.md` say the
  catalog read runs only while the key still says "blank, or the `STRING` placeholder"; M5
  may widen that to include `NULL`. The same paragraph uses `geometry` as the example of a
  real name with no binding — CTO measured at HEAD that `column_type('geometry')` is None on
  all three engines, so that one is right; if M5 changes the marker predicate, the CTO will
  reword. Held until M5 reports.
- **User's call, not mine:** `.lane-t6-scratch/`, `.lane-t7-scratch/`, and
  `.lane-c1-scratch/` are sitting in the repo root, untracked and outside `testpaths`. Rule 11
  bans the delete for the fleet and for me, so they stay until the user removes them. (The
  scratch-in-the-repo ban now lives in RULES.md too; three lanes this round did it before
  the rule existed.)
- **User's call, other:** adding a JS test runner so the frontend can pin a guard. F6's
  follow-up (`components/mapping/`) — a hand-edited row keeps the previous install's tag
  until a save or reload — is currently unverifiable: `frontend/package.json` declares
  `"test": "jest"` but jest is not in `devDependencies` and no `.test.ts*` exists in
  `frontend/src`. Either install jest and dispatch, or carry the item here.
- **Cost, not correctness:** one `GET /schema` walks the catalog twice —
  `effective_mapping()` reflects, then the name page reflects again (`driver_schema.py:126` +
  `:143`). Driver-cached, so nothing is wrong; merging the two needs a cleaner seam than
  either caller has.
- **CTO only:** `createProject` returns only the `Project`, so a caller cannot tell "mapping
  applied" from "mapping rejected"; held because it changes the shape every UI already reads.
- **CTO only, other repo:** graphxr-dev's `databases/proxy/DatabaseProxyAdapter.test.ts` covers
  the relational *flags* (`RELATIONAL_BACKENDS`, `supportsMultiHop`, legacy expansion per
  backend) but never the two-hop statement text, and `proxy/capabilities.ts:184-190`'s claim
  that `all` is "one statement whose SQL fans out the orientations" is untested on both sides.
- **Size register** (measured at `be40901`; source ≤ 1000, split at 800; docs ≤ 500):
  `api/relational.py` 736, `compile.py` 692, `expression.py` 688, `reducer.ts` 723,
  `catalog.py` 654, `mapping.py` 634, `engine_spelling.py` 365, `reflect.py` 308,
  `doc/RELATIONAL_API.md` 414, `doc/RELATIONAL_MAPPING.md` 383, `.claude/RULES.md` 96.
