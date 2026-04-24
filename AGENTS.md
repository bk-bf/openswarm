# openswarm — AGENTS.md

Project-specific rules for AI agents working in this repository.
Global machine rules are in `/home/agent/.config/opencode/AGENTS.md`.

## What this repo is

openswarm is the orchestrator that *spawns and manages* opencode worker
sessions for the yact project. It does not contain application code for yact
itself — that lives under `/home/ubuntu/server/yact/`.

## Repository layout

```
orchestrator.py          — orchestration loop; edit with care (see below)
server.py                — dashboard HTTP server
swarm.sh                 — interactive launcher
prompts/                 — prompt templates for worker/investigator sessions
state.json.template      — reference only; never edit state.json directly
opencode.jsonc.template  — OpenCode agent definitions (dispatcher, orchestrator)
openswarm.service        — systemd unit (orchestrator)
openswarm-dashboard.service — systemd unit (dashboard)
```

**Never touch:**

| Path | Reason |
|---|---|
| `state.json` | Live run state — written atomically by orchestrator; manual edits corrupt runs |
| `swarm.env` | Secrets/runtime args — gitignored, ubuntu-readable only |
| `logs/` | Auto-generated worker logs — gitignored |
| `reports/` | Auto-generated run reports — gitignored |

## Making changes

### Prompt templates (`prompts/`)

These are the prompts injected into every worker and investigator session.
Changes here affect all future runs immediately — no restart needed.

- `worker-template.md`: uses `{{PLACEHOLDER}}` substitution. Valid placeholders
  are defined in `build_worker_prompt()` in `orchestrator.py`.
- `investigator.md`: similarly substituted by `build_investigator_prompt()`.

Do not introduce new placeholders without updating the corresponding builder
function.

### `orchestrator.py`

The orchestrator is long-running and stateful. Key constraints:

- `save_state()` must be called after every mutation to `state["tasks"]` —
  never batch multiple transitions without saving.
- Do not change the `state.json` schema without updating `state.json.template`
  and ensuring `--resume` handles both old and new schemas gracefully.
- `POLL_INTERVAL_SEC`, `WORKER_TIMEOUT_SEC`, and `MAX_ATTEMPTS` are top-level
  constants — adjust there, not inline.

### `server.py`

Pure stdlib HTTP server. No framework dependencies. Keep it that way — it runs
as a systemd service and must start with `python3` alone.

## Testing

There is no automated test suite. To verify changes:

```bash
# Dry-run against the live roadmap (no workers launched)
./swarm.sh --dry-run

# Check the dashboard starts cleanly
python3 server.py &
curl -s http://localhost:7700/api/settings
kill %1
```

For prompt template changes, use `--dry-run` to inspect rendered prompts before
committing.

## Systemd service management

The `agent` user runs both services. To restart after code changes:

```bash
# Dashboard (safe to restart anytime)
sudo systemctl restart openswarm-dashboard   # requires ubuntu to run sudo

# Orchestrator — do NOT restart a live run; only start/stop deliberately
sudo systemctl stop openswarm
sudo systemctl start openswarm
```

Only `systemctl restart openswarm-dashboard` and
`systemctl start/stop openswarm` are in the agent sudoers entry. For anything
else, tell the user the exact command to run.

## Git rules

- **Never commit or push unprompted.** Wait for an explicit instruction.
- **Once commits are authorised for a task, commit after each logical unit of
  work** — do not batch multiple features/fixes into one commit or let
  uncommitted changes pile up across a session. A "logical unit" is one
  coherent change that would stand on its own in `git log`: a single file
  scaffolded, a single bug fixed, a single component ported. When following a
  phased roadmap, prefer the commit shapes listed in that roadmap.
- Always use `YYYY-MM-DD` date format in comments and docs.
- Work on `main` only — there is no PR workflow for this repo.
- `.gitignore` covers `state.json`, `swarm.env`, `logs/`, `reports/`, and
  `__pycache__`. Never stage any of these.
- Before committing, run `git status` and confirm nothing sensitive is staged.
- Commit message style: imperative mood, ≤72 chars, no trailing period.
  Examples:
  - `fix investigator timeout not cancelling worker proc`
  - `add --investigator-model CLI flag`
  - `update worker template to include blocker branch list`

## Troubleshooting protocol

Collect independent evidence before patching code.

Required evidence sources:

- `state.json` — current task statuses, attempt counts, failure reasons, and
  investigator diagnoses
- `logs/<task-id>-worker.log` — full stdout/stderr of the worker session
- `logs/<task-id>-investigator.log` — investigator session output (if a retry
  occurred)
- `logs/<task-id>-worker-prompt.txt` — exact prompt that was sent to the worker
- Sentinel files in the task worktree: `.task-done` / `.task-failed`
- Dashboard API: `curl -s http://localhost:7700/api/settings`

Required workflow:

1. Capture the current `state.json` snapshot and note the failing task IDs.
2. Read `logs/<task-id>-worker.log` for the last error or unexpected exit.
3. Check whether a sentinel file exists and what it contains.
4. If a retry occurred, read `logs/<task-id>-investigator-output.txt` for the
   prior diagnosis.
5. Read the prompt (`logs/<task-id>-worker-prompt.txt`) to confirm placeholders
   substituted correctly.
6. Correlate findings and classify: prompt error, worktree/git failure,
   opencode crash, timeout, sentinel not written.
7. Apply the smallest fix and re-verify with `--dry-run` or a fresh run.

Do not edit `orchestrator.py` to work around a symptom without first
establishing root cause from the evidence above.

## Self-healing loop — how it works

The orchestrator has a built-in two-stage retry mechanism:

1. **Worker** runs up to `MAX_ATTEMPTS` times per task.
2. On first failure, an **investigator** session is spawned synchronously. It
   analyses the worktree and writes a structured diagnosis to
   `logs/<task-id>-investigator-output.txt`.
3. The diagnosis is prepended to the retry worker's prompt under
   `## ⚠ Retry — Investigator Diagnosis`.
4. If the retry also fails the task is marked permanently `failed`.

When debugging a permanently failed task, the investigator diagnosis in
`state.json` (field `investigator_diagnosis`) and the output file are the
primary artefacts. Read them before touching any code.

## Docs (`.docs/`)

`.docs/` is the documentation root for this repo.

| File               | Scope                                               |
| ------------------ | --------------------------------------------------- |
| `bugs/BUGS.md`     | Known bugs and attempted fixes; remove when resolved |
| `DESIGN.md`        | UI design principles for `dashboard.html` — viewport efficiency, no nested borders, no wasted padding |

**Rules:**

- Update docs only when explicitly asked or when a bug is confirmed resolved.
- Always use `YYYY-MM-DD` date format.
- Every doc starts with a `<!-- LOC cap: N (created: YYYY-MM-DD) -->` comment.
- Keep entries brief — this project is early-stage, no elaborate structure needed.

## Verification before declaring work done

After any change to `orchestrator.py`, `server.py`, or a prompt template,
run the following checks before reporting completion:

```bash
# 1. Syntax check
python3 -m py_compile orchestrator.py && echo "syntax OK"
python3 -m py_compile server.py && echo "syntax OK"

# 2. Dry-run — renders prompts and prints the task graph without launching workers
./swarm.sh --dry-run

# 3. Dashboard smoke test
python3 server.py &
curl -s http://localhost:7700/api/settings
kill %1
```

Do not declare a change complete unless all three pass.
