# Progress board

Rolling state only: who is where (collision) and how to pick a lane up
(recoverability). Durable rules and rulings live in `.claude/RULES.md`; what
became true of the product lives in the docs and in `git log`.

## In flight

| Who | Block | Owned paths | Next step | Last commit |
|---|---|---|---|---|
| lane-P1 | `POST /api/relational/{project}/preview` is the route a person runs while their mapping is broken — and the recent round changed three things upstream of it (R8's dialect + array refusal, C1's split key message, M5's marker alignment). The route must still answer a verdict + rows, not 500, when a refusal fires in `compile.py` | the `/preview` region of `src/graphxr_database_proxy/api/relational.py` (`preview_link` + `_preview_pair` + their imports), `src/graphxr_database_proxy/drivers/relational/reads.py` (read-only for the queries), new `tests/test_relational_preview_route.py` | move the three existing `/preview` cases from `tests/test_relational_endpoints.py:413-451` into the new file; add a case per upstream change (array, marker, real-name) parametrised over the three backends; pin `verdict ∈ VERDICTS` with a set equality and `rows ≤ PREVIEW_ROWS_MAX`; do not edit `api/relational.py` outside the preview region; do not edit the docs; survival rule: `git add` the new test file | aa2d2e8 |
| lane-K1 | `services/graph_schema_cache.py` is the process-wide TTL cache behind every driver's `get_graph_schema_cached()`. The doc says "saved mapping invalidates the derived cache", but the three write paths (mapping save, project delete, settings change) are pinned in `api/relational.py:282` and may have gaps | `src/graphxr_database_proxy/services/graph_schema_cache.py`, new `tests/test_graph_schema_cache_invalidation.py` | measure which write paths actually call `invalidate_project`; pin the cache's own contract (get/set/invalidate/TTL); do NOT edit `api/relational.py` or `api/projects.py`; report any defect (e.g. cache stays stale after a project delete) to the CTO rather than fixing it; survival rule: `git add` the new test file | aa2d2e8 |
| lane-M6 (de-dup) | C1's `compile.py:685-688` redefines `_NO_TYPE_MARKERS = frozenset({"", "STRING", "NULL"})`, duplicating `mapping_index.NO_TYPE` (`mapping_index.py:29`). The two are equal today; if one is updated and the other is not, the doc claims of C1/M5 and the property-path behaviour drift. The fix is one import line and a one-line pin | `src/graphxr_database_proxy/drivers/relational/compile.py`, `src/graphxr_database_proxy/drivers/relational/mapping_index.py`, `tests/test_relational_compile.py` (or `tests/test_relational_key_refusal.py`) | consolidate by importing `NO_TYPE` from `mapping_index`; pin the equality with a test; do not change the `_is_no_type_marker` signature; survival rule: any new file `git add`-ed immediately | a64d885 |
| lane-S2 (de-double) | one `GET /schema` walks the catalog twice — `effective_mapping()` reflects, then the name page reflects again (`driver_schema.py:126` + `:143`). Driver-cached, so nothing is wrong; the cost is real, and a future cache-invalidation change exposes a window where the two reflections can disagree | `src/graphxr_database_proxy/drivers/relational/driver_schema.py`, `tests/test_relational_schema_route.py` (existing cases are read-only), new `tests/test_relational_schema_walk.py` | measure: how many `get_multi_*` calls does one `GET /schema` make today; refactor to a single reflection with a per-page column walk; pin the call count with a spy; if a single reflection changes the answer (it shouldn't), report the diff rather than ship; do not touch `services/graph_schema_cache.py` (K1 just landed) or `drivers/relational/compile.py` / `mapping_index.py` / `mapping.py` (M6, C1, M5 still settling); survival rule applies | e226efa |
| lane-T8 (cache lifecycle) | K1 pinned the cache contract; `e226efa` closed the project-delete defect. The end-to-end lifecycle (create → first probe → hit → mapping change → delete → recreate) is not yet pinned at the route layer. A leaked probe, a missed invalidation, or a stale hit would slip past both files | NEW `tests/test_graph_schema_cache_integration.py` | five cases covering the lifecycle, with a probe-count spy and a cache spy; mutations: invalidate no-op, fake zero probe count, ttl=0; do not edit any source file; if the test exposes a defect, report it | 020fabe |
| lane-Q3 (cap constants) | cap constants are scattered (`MAX_LABELS`/`MAX_EDGES`/`MAX_PROPS` in `catalog.py:69-71`, `MAX_MAPPING_BYTES` in `mapping_io.py:32`, `PREVIEW_ROWS_MAX` in `reads.py:38`, `DEFAULT_TTL_SECONDS`/`MAX_ENTRIES` in `graph_schema_cache.py:42,46`) and documented piecemeal in `doc/RELATIONAL_MAPPING.md:81-82,248` and `doc/RELATIONAL_API.md`. A future edit that lowers any cap without updating the doc repeats the D7/D8/D9 drift pattern | NEW `tests/test_relational_caps.py` (under 200 lines) | one pin per constant, with the doc line cross-referenced in the test docstring; mutations to prove each bites; if the doc and the constant disagree, REPORT the drift, do not paper over it by pinning to whichever is "more recent" | 245d875 |
| lane-Q4 (pool contract) | `api/relational.py:282` invalidates the cache AND disposes the engine pool, but the pool half is only spied once (`tests/test_relational_endpoints.py:493`). The cache module has K1's contract; the pool module is its sibling | `src/graphxr_database_proxy/drivers/relational/pool.py` (read), NEW `tests/test_relational_pool_contract.py` | measure today: same `(project_id, config_digest)` reuses, config change rebuilds, delete removes, timeout reaps. Pin each with a test. Do NOT touch `services/graph_schema_cache.py` (K1) or `drivers/relational/driver_schema.py` (S2). Survival rule: any new file `git add`-ed immediately | 90f2917 |
| lane-D10 (API_Reference audit) | D7/D8/D9 audited the relational + USAGE twins. `doc/API_Reference.md` is the only doc not audited this round; Q3's finding (`MAX_ENTRIES=256` was undocumented, fixed in `e0cfee5`) is evidence a sweep is worth doing | READ-ONLY on `doc/`, plus the source the doc references | walk every factual claim in `doc/API_Reference.md` end to end; verify each against the code; report drift; do not write | e0cfee5 |

## Recent

- 2026-10-08 — `aa2d2e8` (lane-D8): **the USAGE twins now describe the same project, with the
  auth surface spelled out.** Six missing Chinese sections translated in (`编辑项目`, `删除项目`,
  `数据模式映射`, `样本数据导出`, `API 密钥认证（可选）`, the OAuth/Service Account flow); the
  Protected vs Public Endpoints table in both twins now lists the 4 admin prefixes (`/api/admin`,
  `/api/embedded`, `/api/relational`, `/api/settings`) with a 2-layer auth preface naming
  `X-Admin-Token` vs `X-API-Key` and pointing at `auth.py:192` and `:249`; the "选择认证方式" step
  in the Chinese is now scoped to Spanner only.
- 2026-10-08 — `09ed493` (CTO): **the key-marker doc now says the marker set and quotes the
  two refusal messages.** The marker set is `{"", "STRING", "NULL"}` (was just `""`/`STRING`
  until `92c8cdb`); the refusal splits into a schema-side case ("the schema did not name
  one") and an engine-side case ("this engine cannot bind as the column's own type") with the
  same remedy tail. C1 reported these as stale; the mapping twins and the API doc were
  re-read against the new code.
- 2026-10-08 — `7efc50f` (lane-C1, redo): **the key refusal names a remedy, and splits the
  marker case from the real-name case.** `_require_bindable_key` raises with two messages;
  both end in the same escape hatch (key on a column the schema can name, or exclude the
  record). 16 new tests pin both branches; mutation A (force marker case to use real-name
  text) → 6 failed; mutation B (the inverse) → 3 failed. R8's `test_relational_unnamed_type`
  / `test_relational_property_types` are unaffected.
- 2026-10-08 — `92c8cdb` (lane-M5, redo): **the key-marker predicate agrees with the property
  path's.** `_key_unnamed` was using a narrower test than `names_a_type`; now both predicate
  paths use the same `{"", "STRING", "NULL"}` set. 327-line new test file pins the agreement
  end to end. The endpoint-record route D6 reported but did not reproduce is now closed
  (the doc drift C1/M5 caused is fixed in `09ed493`).
- 2026-10-08 — `fbbd027` (CTO): **rule 16 + rule 17 land in `.claude/RULES.md`.** 16: a brief's
  stated fact is a claim until it is measured (the round produced two wrong facts in one
  brief); 17: a new file a lane creates is not safe until `git add`-ed, and this round saw
  two lanes (C1, M5) report done then vanish on a session restart — their redo briefs
  carry the instruction.
- 2026-10-08 — `be40901` (CTO): **the API reference now says what `/validate` and `/discover`
  answer and why.** `/validate` gains `provenance`; `/discover` keeps answering none and the
  paragraph says why. Closes the doc drift F6 left when it committed `5b40c4b`.
- 2026-10-08 — `571777b` (lane-R8): **a declared array refuses, a thrown probe is reported,
  and the dialect spells the type.** `type_name(value, dialect=None)`; each engine's
  `column_type` routes an array-shaped declaration to `array_binding` (refuses by name) —
  the silent element-text bug is fixed for MSSQL and MySQL too, not just PG; a thrown probe
  adds to `Gaps` so `catalog.warnings` carries the sentence. CTO verified at HEAD
  `c51ac58` in an isolated tree (1822 passed, 8 skipped).
- 2026-10-06 — `c51ac58` (lane-D6): **the mapping twins now tell a person which type they are
  allowed to state.** Property type survives only where the catalog names none; key type is
  the schema's alone. (Stale by `7efc50b`/`92c8cdb`; the CTO fix is `09ed493`.)
- 2026-10-06 — `87c38ae` (lane-T7): **placement of a same-label link, and of a link the page
  cuts in half.** 18 new cases in `test_relational_placement.py`; mutation proof 18 failed.
- 2026-10-06 — `5b40c4b` (lane-F6): **the check answers the tags of the document it just
  checked, and the editor installs them.** `/validate` now answers `provenance`; `/discover`
  deliberately still answers none. (Doc drift closed in `be40901`.)

## Next up

- **Tentatively done, holding for D9's audit:** the doc twins at `RELATIONAL_MAPPING.md` and
  `.zh.md` were updated to match the new code; D9 will say whether the new wording byte-matches.
  If not, the CTO fixes the diff. Also: D9 will verify the new auth-table rows in USAGE.md
  match the routers' `Depends(...)` decorators.
- **User's call, not mine:** `.lane-t6-scratch/`, `.lane-t7-scratch/`, and
  `.lane-c1-scratch/` are sitting in the repo root, untracked and outside `testpaths`.
  Rule 11 bans the delete for the fleet and for me, so they stay until the user removes them.
- **User's call, other:** adding a JS test runner so the frontend can pin a guard. F6's
  follow-up (a hand-edited row keeps the previous install's tag until a save or reload) is
  currently unverifiable: `frontend/package.json` declares `"test": "jest"` but jest is not
  in `devDependencies` and no `.test.ts*` exists in `frontend/src`. Either install jest and
  dispatch, or carry the item here.
- **Cost, not correctness:** one `GET /schema` walks the catalog twice —
  `effective_mapping()` reflects, then the name page reflects again (`driver_schema.py:126` +
  `:143`). Driver-cached, so nothing is wrong. If K1's measurement finds the cache can be
  stale on a write path it is no longer "just" a cost.
- **CTO only:** `createProject` returns only the `Project`, so a caller cannot tell "mapping
  applied" from "mapping rejected"; held because it changes the shape every UI already reads.
- **CTO only, other repo:** graphxr-dev's `databases/proxy/DatabaseProxyAdapter.test.ts` covers
  the relational *flags* but never the two-hop statement text, and `proxy/capabilities.ts:184-190`
  claims `all` is "one statement whose SQL fans out the orientations" — untested on both
  sides. This repo proves the outcome (`ccf810f`), not the single-statement shape.
- **Size register** (measured at `aa2d2e8`; source ≤ 1000, split at 800; docs ≤ 500):
  `api/relational.py` 736, `compile.py` 692, `expression.py` 688, `reducer.ts` 723,
  `catalog.py` 654, `mapping.py` 634, `engine_spelling.py` 365, `reflect.py` 308,
  `doc/RELATIONAL_MAPPING.md` 394 / `.zh.md` 362, `doc/USAGE.md` 350 / `.zh.md` 362,
  `doc/RELATIONAL_API.md` 414, `.claude/RULES.md` 96.
