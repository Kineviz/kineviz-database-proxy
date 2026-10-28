# Project instructions

Shared guidance for all AI coding tools. Keep tool-specific entry files thin;
update project conventions here rather than copying them between tools.

## Project and commands

Kineviz Database Proxy is a FastAPI backend with a React 18 + antd TypeScript
admin UI. One production process serves the API and the built UI.

Run commands from the repository root. Use `uv` and `.venv` for Python; use npm
for JavaScript. Setup and tool entry points: [development guide](doc/DEV_GUIDE.md).

| Task | Command |
| --- | --- |
| Install Python development dependencies | `uv sync --extra dev` |
| Install JavaScript dependencies | `npm install` then `npm run install:frontend` |
| Develop both services | `npm run dev` |
| Develop one service | `npm run dev:backend` / `npm run dev:frontend` |
| Backend tests | `npm test -- -q` |
| Targeted backend tests | `npm test -- tests/test_dialect.py -k expand -q` |
| Frontend type check | `npm run typecheck` |
| Frontend production build | `npm run build` |
| Full local verification | `npm run verify` |
| Serve API and built UI on port 9080 | `npm start` |

`npm test` uses the installed Python environment without syncing dependencies.
Run setup again when dependencies change. Tests are offline by default; enable
`GRAPHXR_PROXY_ENGINE_TESTS=1` only for intentional engine download/integration
runs. Async pytest tests need no decorator (`asyncio_mode = "auto"`).

## Where to work

- `src/kineviz_database_proxy/api/`: FastAPI routes.
- `src/kineviz_database_proxy/drivers/`: database drivers, shared dialects and intents.
- `src/kineviz_database_proxy/drivers/relational/`: mapping, Cypher-to-SQL pipeline,
  catalog and engine spellings for SQL Server, PostgreSQL and MySQL.
- `src/kineviz_database_proxy/drivers/embedded/`: isolated engine workers and stores.
- `src/kineviz_database_proxy/models/`, `services/`: models and JSON persistence.
- `src/kineviz_database_proxy/contract/`: vendored GraphXR contract schema.
- `frontend/src/`: TypeScript UI; `tests/`: backend tests; `examples/`: Python usage.

Read [architecture notes](doc/ARCHITECTURE.md) when changing drivers, graph identity,
router ordering, embedded runtimes or the GraphXR contract. Mapping semantics live
in [RELATIONAL_MAPPING](doc/RELATIONAL_MAPPING.md); route details in
[API_Reference](doc/API_Reference.md) and [RELATIONAL_API](doc/RELATIONAL_API.md).

## Change and verification rules

- Check `git status --short` before editing. Preserve changes from the user or
  other sessions. Keep edits scoped to the requested work.
- Match nearby style; do not reformat unrelated code. New frontend files use
  TypeScript. Code, comments and commit messages are English; reply in the user's
  language. Chinese documentation is welcome alongside its English counterpart.
- Run targeted tests while developing and the backend suite for backend changes.
  Run type checking and a production build for frontend changes. For documentation
  changes, check local links and keep existing English/Chinese pairs aligned.
  Report the commands actually run, failures and skipped checks.
- Verify claims against current code and actual command output. Historical plans,
  board entries and another agent's report are context, not verification.
- Page collection reads at the source (cursor or limit/offset); never fetch an
  entire collection merely to slice it in memory.
- Split modules by responsibility; aim to split source files near 800 lines,
  keep them below 1000, and keep individual docs below 500 lines. Preserve public
  imports when moving code and check for circular imports.
- Respect the user's requested workflow and the tool's permissions; do not require
  a fixed number of agents, a shared worktree or a tool-specific workflow.
- Commit only when requested. Stage explicit paths, never `git add -A`. Leave
  unrelated staged changes alone. Push, publish, PR creation and history rewrites
  require an explicit request. Use the developer's configured Git identity.

## Local state and secrets

Do not read, edit or commit live credentials or user data: `.env` and local env
overrides (including under `frontend/`), `config/projects.json`,
`config/settings.json`, `config/*credentials*.json`, `config/*oauth*.json`,
`config/service-account.json`, `config/databases/`, `config/engines/`, and engine
installations under `~/.graphxr-proxy/`. Use `.env.example` and
`config/projects.example.json` as references. Keep tests in temporary directories.

Avoid destructive cleanup or overwriting local work. Do not store personal tokens,
machine paths or personal permission overrides in shared AI configuration.
Instruction files guide behavior; each tool's own sandbox and permissions enforce
access. Claude-specific permissions/hooks do not configure other tools.
