# openswarm

Autonomous multi-agent task orchestrator for the **yact** project. Reads a
dependency graph of roadmap tasks (`ROADMAP_DEPS.json`), resolves the DAG,
spawns parallel `opencode` worker sessions per task, monitors for completion
via sentinel files, and retries failures with an investigator session that
diagnoses what went wrong.

## How it works

1. **Orchestrator** (`orchestrator.py`) reads `ROADMAP_DEPS.json` from the yact
   workspace and builds a dependency DAG.
2. For each ready task it creates a git worktree in the target repo(s), builds a
   prompt from `prompts/worker-template.md`, and launches `opencode run` as a
   background process.
3. Workers signal completion by writing `.task-done` or `.task-failed` to their
   worktree root. The orchestrator polls every 60 s.
4. On failure an **investigator** session (`prompts/investigator.md`) analyses
   the worktree and produces a diagnosis; the task is re-queued for one retry
   with the diagnosis prepended to the prompt.
5. After all tasks reach a terminal state a Markdown run report is written to
   `reports/`.

## Files

```
orchestrator.py          — core orchestration loop
server.py                — dashboard HTTP server (port 7700)
swarm.sh                 — interactive launcher (checks python3/opencode/deps)
prompts/
  worker-template.md     — prompt template for worker sessions
  investigator.md        — prompt template for investigator sessions
state.json               — live run state (gitignored, auto-written)
state.json.template      — example state structure for reference
swarm.env                — runtime env vars for systemd (gitignored)
logs/                    — per-task worker/investigator logs (gitignored)
reports/                 — Markdown run reports (gitignored)
openswarm.service        — systemd unit for the orchestrator
openswarm-dashboard.service — systemd unit for the dashboard server
```

## Usage

### Interactive

```bash
# Tier 1 default scope (T-210, T-211, T-212)
./swarm.sh

# Explicit scope
./swarm.sh --scope T-210,T-211,T-212

# Resume an interrupted run
./swarm.sh --resume

# Expand scope on resume
./swarm.sh --resume --scope T-213,T-214,T-215,T-216

# Preview task graph and prompts without launching workers
./swarm.sh --dry-run
```

`swarm.sh` validates that `python3 >= 3.11`, `opencode`, and
`ROADMAP_DEPS.json` are available before handing off to `orchestrator.py`.

### Direct (orchestrator)

```bash
python3 orchestrator.py --workspace /path/to/yact [options]
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--workspace` | `/home/ubuntu/server/yact` | Path to the yact metarepo root |
| `--roadmap` | `<workspace>/yact-dev-docs/.tasks/open/ROADMAP_DEPS.json` | Path to ROADMAP_DEPS.json |
| `--scope` | `T-210,T-211,T-212` | Comma-separated task IDs |
| `--resume` | off | Resume from `state.json` |
| `--model` | `github-copilot/claude-sonnet-4.6` | Default model for workers |
| `--investigator-model` | same as `--model` | Model for investigator sessions |
| `--dry-run` | off | Print graph/prompts without launching workers |

### Systemd services

```bash
# Install (run once as ubuntu)
sudo cp openswarm.service openswarm-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload

# Dashboard — always-on, auto-restarts
sudo systemctl enable --now openswarm-dashboard

# Orchestrator run — start manually per-run (does not auto-restart)
sudo systemctl start openswarm

# Follow logs
journalctl -u openswarm -f
journalctl -u openswarm-dashboard -f
```

Configure runtime arguments by editing `swarm.env` (set `SWARM_ARGS`).

## Dashboard

`server.py` serves `dashboard.html` as a static file and exposes two API
endpoints:

- `GET /api/models` — lists models from the connected opencode server
- `GET /api/settings` / `POST /api/settings` — read/write `settings.json`

Default port: **7700** (override with `OPENSWARM_PORT` env var).
Expects the opencode server at `http://localhost:4097` (override with
`OPENCODE_API` env var).

## State file

`state.json` is written atomically after every state transition. It tracks
status, attempt count, branch names, worktree paths, sentinel messages, and
timestamps for every task. Safe to kill and resume with `--resume`.

To start fresh: `rm state.json && ./swarm.sh`

## Requirements

- Python 3.11+
- `opencode` on PATH
- Git with worktree support
- The yact metarepo at `--workspace` with `ROADMAP_DEPS.json` present
