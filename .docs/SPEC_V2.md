<!-- LOC cap: 380 (created: 2026-04-22) -->
# openswarm v2 — Design Spec

## What openswarm is (revised)

openswarm is a **multi-session OpenCode dashboard** with a built-in
task-distribution orchestrator. Think opencode-web with parallel split-card
sessions, plus an always-on Hermes agent that watches a task queue and
spawns/monitors workers automatically.

Two halves:

| Half | Role |
|---|---|
| **Worker cards** | One card = one OpenCode session. Displays session chat, status, worktree. User can spawn manually or via orchestrator. |
| **Hermes orchestrator** | Always-on Hermes cron agent. Reads `tasks.json`, distributes work to OC workers, monitors completion sentinels. Does NOT write code itself. |

---

## 1. Worker cards

### What a card represents

A card is a thin wrapper around an OpenCode session. It holds:

| Field | Source | Notes |
|---|---|---|
| `session_id` | OC sqlite DB | Resolved at load time: `SELECT id FROM session WHERE directory = card.dir ORDER BY time_updated DESC LIMIT 1` |
| `label` | User-set | Short display name ("T-212 btc-dominance") |
| `dir` | User-set or task-derived | Absolute path OC session runs in (the worktree) |
| `worktree` | Optional | Git worktree name, shown as badge |
| `status` | sentinel file | pending / running / done / failed |

Cards no longer have a `README.json`. There is no instantiation prompt file.
The user types their task directly into the card's chat input (like opencode-web).

> **Confirmed:** OC `session` table has a `directory TEXT NOT NULL` column.
> Direct lookup is reliable — no heuristic needed.

### Card layout (matches screenshot)

```
┌────────────────────────────────────────────────────────────┐
│ label       worktree-badge   attempt N/N  Xm Xs  STATUS [⊙]│
│ dir path (dim)                                              │
│────────────────────────────────────────────────────────────│
│  session chat (scrollable, streamed from OC DB)            │
│────────────────────────────────────────────────────────────│
│  [chat input textarea]                           [send]    │
└────────────────────────────────────────────────────────────┘
```

### Card lifecycle

1. **Manual spawn** — user clicks `+` in the dashboard, fills onboarding form
   (label, dir, optional worktree name). Server runs
   `opencode run --dir <dir> --title <label>`. Card appears immediately.

2. **Orchestrator spawn** — Hermes picks a pending task from `tasks.json`,
   runs `opencode run --dir <dir> --title <label> -c <prompt>`, then POSTs
   `{"task_id": "T-220", "dir": "...", "label": "..."}` to
   `POST /api/cards` so the dashboard registers the card.

3. **Completion** — orchestrator detects `.task-done` or `.task-failed`
   sentinel, updates `tasks.json` status, POSTs status to
   `POST /api/orchestrator/status`. Card status badge updates.

4. **Close** — user clicks ✕ on the card header. Server moves the card entry
   from `cards.active` to `cards.history` in `cards.json`. Card disappears
   from the grid. Restorable via history dropdown.

> **Confirmed:** `opencode run --dir <path> --title <title> -c <prompt>`
> all flags exist and work. No `--dir` gap.

---

## 2. Task queue — `tasks.json`

Replaces `ROADMAP_DEPS.json` and `state.json`.

**Location:** `<project-dir>/.openswarm/tasks/tasks.json` — one file per monitored project.  
**Who writes what:** user adds/removes tasks; orchestrator updates status fields only.

### Format

```json
{
  "tasks": [
    {
      "id": "T-220",
      "label": "Add funding rates chart",
      "dir": "/home/ubuntu/server/yact/yact-web/features/t-220-funding-rates",
      "worktree": "features/t-220-funding-rates",
      "prompt": "Implement a funding rates chart on the coin detail page. See AGENTS.md.",
      "deps": [],
      "model": "github-copilot/claude-sonnet-4.6",
      "status": "pending",
      "attempts": 0,
      "max_attempts": 2,
      "spawned_session_id": null,
      "started_at": null,
      "finished_at": null,
      "failure_reason": null
    }
  ]
}
```

Key design decisions:

- **User populates `tasks`** — no scripts generate tasks. User edits the JSON
  directly (or via a future UI). This is the only input mechanism.
- **Orchestrator only writes** `status`, `attempts`, `spawned_session_id`,
  `started_at`, `finished_at`, `failure_reason`. All other fields are
  user-owned and must never be overwritten by the orchestrator.
- **`dir`** is the absolute path the OC worker session runs in. If the worktree
  doesn't exist yet, the orchestrator creates it with `git worktree add
  <dir> <worktree>`.
- **`prompt`** is the first `-c` message sent to the OC worker session. Keep it
  self-contained — include all context the worker needs.
- **`deps`** is a list of task IDs that must be `done` before this task starts.

### State transitions

```
pending → running → done
                 ↘ failed  (after max_attempts; stays failed, user re-queues)
```

No investigator retry in v2. Simplicity over cleverness.

### Sentinel files

Worker OC sessions write a sentinel when finished:

- `.task-done` — success
- `.task-failed` — failure (optionally with a one-line reason as file content)

Written into `<task.dir>/`. The orchestrator checks for these on each tick.
This mechanism already works and is confirmed in use for T-210 / T-211.

---

## 3. Hermes orchestrator

### Purpose (strict)

The Hermes orchestrator does exactly three things on each cron tick:

1. **Read** `tasks.json` — find pending tasks whose deps are all done.
2. **Spawn** `opencode run --dir <dir> --title <label> -c <prompt>` for
   each ready task; record `spawned_session_id` + `started_at`.
3. **Check** running tasks for sentinel files; update `status` + `finished_at`.

It does **not** write code, edit project files, run tests, or implement
anything itself.

### Tool restrictions — what's actually enforceable

> **Correction vs. initial spec:** Hermes `tools disable` is **global per
> platform** (cli/telegram/discord), not per cron job. There is no
> per-job tool whitelist in Hermes. Restriction at the Hermes level is
> **prompt-only** (via skill file).

The actual enforceable restriction is on the **OpenCode `.orch-session`**
side, where the user interacts with the orchestrator via the orch panel chat.
OpenCode supports custom agents with a tool whitelist via `opencode agent
create --tools "bash,read,write"`. A `dispatcher` agent defined in
`opencode.jsonc` restricts the OC session the user sees to only the tools
the orchestrator needs.

Two-layer approach:

| Layer | Mechanism | Enforcement |
|---|---|---|
| Hermes cron skill | Skill file prompt (`~/.hermes/skills/openswarm-<id>.md`) | Prompt-based only — not enforced at tool level |
| OC `.orch-session` | Custom `dispatcher` agent in `opencode.jsonc` | Hard — OC permission system, write/edit denied |

The `dispatcher` agent definition (added to project `opencode.jsonc`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "agents": {
    "dispatcher": {
      "model": "github-copilot/claude-sonnet-4.6",
      "tools": ["bash", "read", "write"],
      "system": "You are the openswarm task dispatcher. You read tasks.json, spawn opencode workers via bash, and update tasks.json status fields. You MUST NOT write or edit any file other than tasks.json. You MUST NOT implement features yourself."
    }
  }
}
```

The `.orch-session` is started with:
```
opencode run --dir <project>/.orch-session --agent dispatcher
```

> **Confirmed:** `opencode agent create --tools` accepts a comma-separated
> list from: `bash, read, write, edit, list, glob, grep, webfetch, task,
> todowrite`. The `dispatcher` agent omits `edit`, `glob`, `grep`, `webfetch`,
> `task`, `todowrite`. This is a hard OC-level restriction.

### Hermes skill file

Stored at `~/.hermes/skills/openswarm-<id>.md`. Content injected as system
context for every cron tick. Prompt-based restrictions (Hermes cannot enforce
tool-level):

```markdown
## Role
You are the openswarm task dispatcher for <label> (<dir>).
Your ONLY job: read <dir>/tasks.json, spawn opencode workers, record results.

## What you do on each tick
1. Read <dir>/tasks.json
2. For each task where status=pending and all deps are done:
   - Create git worktree if needed: git worktree add <dir> <worktree>
   - Run: opencode run --dir <task.dir> --title "<task.label>" -c "<task.prompt>"
   - Update tasks.json: set status=running, spawned_session_id=<id>, started_at=<iso>
3. For each task where status=running:
   - Check for <task.dir>/.task-done → set status=done, finished_at=<iso>
   - Check for <task.dir>/.task-failed → set status=failed, failure_reason=<content>
4. POST status summary to http://localhost:7700/api/orchestrator/status

## Hard restrictions (prompt-level)
- Do NOT edit any file except tasks.json
- Do NOT run tests, write code, or fix bugs yourself
- Do NOT use Write/Edit on project source files
- If a task is ambiguous, mark it failed with reason "ambiguous prompt" — do not guess
```

### Cron tick injection via `--script`

> **Found:** Hermes cron supports `--script <path>` — a Python script whose
> stdout is injected into the prompt at each run. Use this to provide fresh
> task state without the orchestrator needing to call Read on startup:

```python
# ~/.hermes/scripts/openswarm-<id>.py
import json, sys
tasks = json.load(open('/path/to/tasks.json'))
pending = [t for t in tasks['tasks'] if t['status'] == 'pending']
running = [t for t in tasks['tasks'] if t['status'] == 'running']
print(f"PENDING: {len(pending)}, RUNNING: {len(running)}")
for t in pending + running:
    print(f"  {t['id']} {t['label']} deps={t['deps']} dir={t['dir']}")
```

This reduces the Hermes context window usage — the orchestrator gets a
pre-digested summary instead of reading the full JSON itself.

### Cron cadence

- Default: every 2 minutes.
- Only runs if `settings.json` has `"autonomous": true` (checked by the
  `--script` output or by the skill preamble reading settings.json).
- Paused/resumed via `hermes cron pause/resume <job-id>` — no change here.

### `poll_tasks.py` — keep or drop?

> **Recommendation: keep as a thin shell, have Hermes call it.**

Hermes with `terminal` toolset can run arbitrary bash. Having Hermes call
`python3 /path/to/poll_tasks.py --workdir <dir>` as a single shell command
is simpler and more reliable than having Hermes implement the dispatch logic
itself (where prompt drift could cause it to do the wrong thing). The Python
script is deterministic; Hermes is not.

In v2, `poll_tasks.py` is refactored to use `tasks.json` instead of
`ROADMAP_DEPS.json` + `state.json`. Hermes still calls it via bash. This is
the **safer migration path**.

---

## 4. Dashboard — info flow

### Session resolution (corrected)

On load, for each active card in `cards.json`:

```sql
SELECT id FROM session
WHERE directory = :card_dir
ORDER BY time_updated DESC
LIMIT 1
```

That's it. No heuristics, no time-windowed guessing. Multiple OC sessions
can exist per directory — we always show the newest.

> **Note:** Multiple sessions per directory is normal (confirmed: yact dir
> has 24 sessions). The newest-wins rule is correct for resumed work.

### Orch panel session — corrected wiring

`orchSessionId` null bug root cause: `refreshHermesSession` looks for a
session in `<project-dir>/.orch-session` but that subdirectory may not have
been created or may have a stale session. New behaviour:

1. Look up newest OC session with `directory = <project-dir>/.orch-session`.
2. If found → wire `orchSessionId` to it, show chat.
3. If not found → show "no orchestrator session" placeholder with a
   "start session" button. On click: run
   `opencode run --dir <project>/.orch-session --agent dispatcher`.
4. Never auto-spawn silently on panel open — only on explicit user action.

This eliminates the null race condition entirely.

### `projects.json` — replaces `sessions.json`

> **Correction:** `sessions.json` cannot be fully replaced by `cards.json`
> — it holds the Hermes cron job mapping (which cron job ID corresponds to
> which project directory). Rename it `projects.json` for clarity.

```json
[
  {
    "id": "7b99bb70",
    "dir": "/home/ubuntu/server/yact",
    "label": "yact",
    "cron_job_id": "713db8009d1d",
    "created_at": "..."
  }
]
```

`skill` field is derived (`openswarm-<id>`) and doesn't need to be stored.

### API surface (new/changed endpoints)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/tasks?project=<id>` | Read `tasks.json` for a project |
| POST | `/api/tasks?project=<id>` | Atomic write of `tasks.json` |
| GET | `/api/cards` | Read `cards.json` (active + history) |
| POST | `/api/cards` | Add a card (manual or orchestrator spawn) |
| DELETE | `/api/cards/<id>` | Close card → history |
| POST | `/api/cards/<id>/restore` | Restore from history |

Existing endpoints (`/api/oc/sessions`, `/api/oc/session/*/messages`,
`/api/oc/session/*/send`, `/api/orchestrator/status`) are unchanged.

### `cards.json`

Global — one file at `/home/ubuntu/server/openswarm/cards.json`.
Covers cards from all monitored projects.

```json
{
  "active": [
    {
      "id": "card-abc123",
      "label": "T-220 funding rates",
      "dir": "/home/ubuntu/server/yact/yact-web/features/t-220-...",
      "worktree": "features/t-220-funding-rates",
      "project_id": "7b99bb70",
      "task_id": "T-220",
      "created_at": "2026-04-22T..."
    }
  ],
  "history": []
}
```

`project_id` links back to the project in `projects.json`.
`task_id` links to the task in that project's `tasks.json` (null for manually spawned cards).

---

## 5. What gets deprecated / removed

| Item | Replacement | Notes |
|---|---|---|
| `ROADMAP_DEPS.json` | `tasks.json` | Simpler schema, user-populated |
| `state.json` | `tasks.json` (status fields merged in) | |
| `poll_tasks.py` | Refactored to read `tasks.json`, still called by Hermes | Not removed — kept as deterministic shell |
| `orchestrator.py` / `openswarm.service` | Already gone | |
| Worker `README.json` files | `tasks[].prompt` field | |
| `sessions.json` | `projects.json` (renamed, `skill` field dropped) | |
| `resolveSessionId` heuristic | Direct `directory` SQL lookup | |
| `ocSessionList` cache keyed by time | Simple newest-session-per-dir lookup | |

---

## 6. Decisions — confirmed 2026-04-22

1. **`tasks.json` location**: `<project-dir>/.openswarm/tasks/tasks.json`
   (inside the managed repo, under `.openswarm/`).

2. **`poll_tasks.py`**: Keep it. Hermes calls it as a bash command
   (`python3 /path/to/poll_tasks.py --workdir <dir>`). Deterministic Python
   is more reliable than asking an LLM to implement the dispatch logic.

3. **Worker agent restriction**: None. Worker cards use the full `build` agent
   (all tools). Only the `.orch-session` is restricted to `dispatcher`.

4. **`cards.json` scope**: Global — single file at
   `/home/ubuntu/server/openswarm/cards.json`. Simpler for the dashboard.

---

## 7. Implementation sequence

1. **`tasks.json` + `projects.json`** — define schemas, write
   `GET/POST /api/tasks`, rename `sessions.json` → `projects.json` in
   `server.py`, update references.
2. **`cards.json` + grid rewrite** — CRUD endpoints, wire dashboard grid
   to `cards.json` instead of `state.json`. Session resolution via direct
   SQL directory lookup.
3. **Manual spawn flow** — `+` button → onboarding form → `POST /api/cards`
   → `opencode run --dir <dir> --title <label>` → card appears.
4. **Close / history** — ✕ on card → `DELETE /api/cards/<id>` → history
   dropdown restores.
5. **Orch panel chat fix** — remove auto-spawn, add "start session" button,
   wire to `dispatcher` agent.
6. **`poll_tasks.py` refactor** — replace `ROADMAP_DEPS.json`/`state.json`
   reads with `tasks.json`. Update Hermes skill to call the new script.
7. **`dispatcher` agent** — add to `opencode.jsonc`, test tool restriction.
8. **Dead code removal** — `resolveSessionId`, `ocSessionList` time heuristic,
   old `state.json` schema, `ROADMAP_DEPS.json` references.

Steps 1–4 are independently deployable and unblock the manual-spawn UX
immediately. Steps 5–8 can follow without blocking the user.
