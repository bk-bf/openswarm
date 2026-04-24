<!-- LOC cap: 150 (created: 2026-04-21) -->
# Orchestrator Redesign — Hermes-based Architecture

## Problem

`orchestrator.py` is a one-shot Python script. It is not an always-on process;
it only runs when explicitly launched. The openswarm dashboard (incorrectly)
shows the OpenCode session that *is running openswarm itself* as the
"orchestrator session" (BUG-002). The orchestrator has no persistent identity
and cannot be communicated with from the dashboard between runs.

## Goal

Replace `orchestrator.py` with **Hermes Agent** running as an always-on
background service. Hermes is purpose-built for this role: it is persistent,
model-agnostic, has native cron scheduling, and can shell out to external
processes (opencode workers) via its terminal tools.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  systemd: hermes-gateway.service  (always-on)            │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Hermes Agent  (model: configurable)               │  │
│  │  Skill: openswarm                                  │  │
│  │  Cron: poll-tasks (every 2m)                       │  │
│  │                                                    │  │
│  │  On each tick:                                     │  │
│  │  1. Read tasks.json (formerly ROADMAP_DEPS.json)   │  │
│  │  2. Resolve DAG — find tasks whose deps are done   │  │
│  │  3. For each ready task: run opencode worker       │  │
│  │  4. Monitor sentinel files (.task-done/.task-fail) │  │
│  │  5. On completion: spawn verify worker (if set)    │  │
│  │  6. POST status updates to dashboard API :7700     │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘

┌──────────────────────┐    HTTP    ┌──────────────────────┐
│  dashboard (port 7700│◄──────────│  Hermes (status POST) │
│  server.py           │           └──────────────────────┘
│  dashboard.html      │
└──────────────────────┘
```

## Components

### 1. tasks.json  (replaces ROADMAP_DEPS.json)

New location: `/home/ubuntu/server/yact/yact-dev-docs/.tasks/open/tasks.json`

Format change — each task gets two new optional fields:

```json
{
  "id": "T-210",
  "tier": 1,
  "slug": "surface-oi",
  "model": "github-copilot/claude-sonnet-4.6",
  "title": "Surface open interest on coin detail page",
  "repos": ["web"],
  "blocked_by": [],
  "doc_refs": [],
  "description": "...",
  "verify": true,
  "verify_prompt": "Run pnpm test and confirm the open interest component renders without errors."
}
```

- `verify` (bool, default `false`) — whether to spawn a verification worker
  after the task worker marks `.task-done`
- `verify_prompt` (string, optional) — specific prompt for the verify worker;
  defaults to a generic "run tests and confirm the change works" prompt

### 2. Hermes skill: openswarm

Location: `~/.hermes/skills/openswarm.md`

Teaches Hermes:
- Where `tasks.json` lives
- How to read the DAG and compute ready tasks
- The `opencode run` command signature for spawning workers
- Where sentinel files are written (`.task-done` / `.task-failed` in worktree)
- Dashboard API endpoints to POST status to
- What to include in the verify worker prompt

### 3. Hermes cron job: poll-tasks

Registered with: `hermes cron create "every 2m" "Check for ready tasks" --skill openswarm`

On each tick, the Hermes agent:
1. Reads `tasks.json` and `state.json`
2. Identifies tasks that are `pending` and whose `blocked_by` are all `done`
3. For each: shells out `opencode run -c "<worker prompt>" -m "<model>" -s "T-xxx"`
4. Polls sentinel files until worker exits
5. Updates `state.json` (same schema, same `save_state()` semantics)
6. POSTs to `POST /api/task/<id>/status` on the dashboard
7. If `verify: true`, spawns a verification `opencode run` session

### 4. systemd service: hermes-gateway.service

Replaces `openswarm.service`. Runs `hermes gateway start` under the `agent` user.

```ini
[Unit]
Description=Hermes Agent Gateway (openswarm orchestrator)
After=network.target

[Service]
User=agent
WorkingDirectory=/home/agent/.hermes/hermes-agent
ExecStart=/home/agent/.hermes/hermes-agent/hermes gateway start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 5. Dashboard changes (server.py)

Add two new API endpoints:
- `POST /api/task/<id>/status` — accepts `{status, message}`, updates state.json
  and broadcasts via SSE to the dashboard UI
- The existing orchestrator-session panel should display Hermes' own session ID
  (stored in a new `hermes_session_id` field in `state.json`) — fixes BUG-002

## Migration

1. Rename `ROADMAP_DEPS.json` → `tasks.json`; add `verify`/`verify_prompt` fields
2. Install Hermes skill `openswarm.md`
3. Register the `poll-tasks` cron job
4. Install `hermes-gateway.service` via `sudo hermes gateway install --system`
5. Stop `openswarm.service`, start `hermes-gateway.service`
6. Update dashboard to use new `POST /api/task/<id>/status` for status display

## What stays the same

- `state.json` schema (Hermes updates it via the same `save_state()` logic, now
  called from within the skill via `execute_code`)
- Worktree paths and sentinel file conventions
- `server.py` / `dashboard.html` (dashboard stays on port 7700)
- `prompts/worker-template.md` and `prompts/investigator.md` content

## What is removed

- `orchestrator.py` — superseded by Hermes skill + cron
- `openswarm.service` — superseded by `hermes-gateway.service`
- `swarm.sh` — replaced by `hermes cron run poll-tasks` for manual trigger
