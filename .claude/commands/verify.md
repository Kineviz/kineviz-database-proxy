---
description: Verify the current change using the shared project checks
allowed-tools: Bash(npm test *), Bash(npm run typecheck *), Bash(npm run build *), Bash(git status *), Bash(git diff *)
---

Follow `AGENTS.md` and inspect `git diff` plus `git diff --cached` to identify
the change. Verify without committing:

1. Backend or test changes: `npm test -- -q`.
2. Frontend changes: `npm run typecheck` and `npm run build`.
3. Documentation changes: check local links and any existing English/Chinese pair.
4. `git status --short`: distinguish this task's files from unrelated work.

Report checks run, skipped checks and failures with the relevant output.
