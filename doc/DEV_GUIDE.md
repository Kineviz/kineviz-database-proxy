# Development guide

[English](DEV_GUIDE.md) | [中文](DEV_GUIDE.zh.md)

## Setup and run

Requires Python >= 3.9, Node.js >= 22 and [uv](https://docs.astral.sh/uv/).
From the repository root:

```bash
uv sync --extra dev
npm install
npm run install:frontend
npm run dev
```

Repeat dependency installation after changing dependency files. Relational
connections also need the relevant extra, e.g. `uv sync --extra dev --extra postgresql`;
use `--extra relational` to install all four SQL drivers, including Snowflake.
For Snowflake alone, use `--extra snowflake`; see [Snowflake setup](SNOWFLAKE.md).

The backend runs at http://localhost:9080 (API docs at `/docs`). The frontend
uses `PORT` if set, otherwise webpack selects an available port; use the URL
printed in its output. API requests are proxied to port 9080.

`npm run dev:backend` and `npm run dev:frontend` start either service separately.
Both support hot reload; enter `rs` in the nodemon console to restart the backend.
For a production-style local run, use `npm run build` followed by `npm start`.

If Windows PowerShell blocks `npm.ps1`, use `npm.cmd` in place of `npm`
without changing the machine's execution policy.

## AI coding tools

[AGENTS.md](../AGENTS.md) is the single source for project instructions.
Tool-specific files only load or point to it:

| Tool | Project entry |
| --- | --- |
| Codex | `AGENTS.md`, discovered automatically |
| Claude Code | `CLAUDE.md` imports `@AGENTS.md` |
| Cursor | `AGENTS.md`, discovered automatically |
| GitHub Copilot | `.github/copilot-instructions.md` directs it to read `AGENTS.md` |
| Gemini CLI | `GEMINI.md` imports `@./AGENTS.md` |
| Other assistants | Ask the tool to read `AGENTS.md` before making changes |

Start Codex or Claude Code from the repository root. Keep personal credentials,
model choices and permission overrides in the tool's own local configuration.
Shared Claude permissions and its commit-message hook remain in
`.claude/settings.json`; they apply to Claude only. Instruction files are guidance,
not a security boundary.

Official loading behavior: [Codex](https://developers.openai.com/codex/guides/agents-md/),
[Claude Code](https://code.claude.com/docs/en/memory),
[Cursor](https://cursor.com/docs/rules),
[Copilot](https://docs.github.com/en/copilot/reference/custom-instructions-support),
[Gemini CLI](https://geminicli.com/docs/cli/gemini-md/).

## Verify changes

| Scope | Command |
| --- | --- |
| Backend | `npm test -- -q` |
| Focused backend test | `npm test -- tests/test_dialect.py -k expand -q` |
| Frontend types | `npm run typecheck` |
| Frontend bundle | `npm run build` |
| All local checks | `npm run verify` |

`npm test` runs pytest in the installed `.venv` without syncing dependencies.
The default suite is offline. Engine integration tests require an explicit
`GRAPHXR_PROXY_ENGINE_TESTS=1` and may download runtimes. Webpack does not perform
TypeScript checking, so frontend changes need both frontend checks. There is no
installed frontend unit-test runner.

Claude's `/verify` selects checks by the changed files and reports results.
For documentation changes, check local links and existing English/Chinese pairs.

### PostgreSQL 18 sample integration

With the proxy running and GraphXRSupportDB's pg18 sample available, run:

```sh
uv sync --extra dev --extra postgresql --inexact
uv run --no-sync python scripts/check_pg18_proxy.py --connection-file ../GraphXRSupportDB/connections/pg18.local.json
```

The script creates `pg18-supportdb` through the admin API if it is absent and
reuses an existing project without replacing its configuration. It reads the
sample database without resetting or modifying it. Fifteen checks compare HTTP
results with direct SQL, covering schema discovery, counts, two-hop queries,
Expand, category/relationship pulls, special characters and write rejection.
Credentials stay out of its output. Use `--proxy-url` or `--project` to override
the defaults, and `--mapping-out` to export a credential-free mapping such as
[the pg18 example](../examples/pg18.graph-mapping.json).

To import and check a specific JSON/YAML file, add `--mapping-file` with its path.
This explicitly replaces the project's mapping through the admin API and also
checks JSON/YAML download and re-import. The source-format example is
[pg18.graphxr-mapping.json](../examples/pg18.graphxr-mapping.json).

## Further reading

- [Architecture and driver extension](ARCHITECTURE.md)
- [Usage](USAGE.md) · [API reference](API_Reference.md)
- [Relational mapping](RELATIONAL_MAPPING.md) · [Embedded stores](EMBEDDED_STORES.md)
- [Docker publishing](DOCKER_PUBLISHING.md) · [PyPI publishing](PYPI_PUBLISHING.md)

`docs/superpowers/` contains historical designs and session notes. Read them when
relevant; they do not prescribe current tasks, agent counts or workflow.
