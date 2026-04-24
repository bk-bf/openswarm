<!-- LOC cap: 300 (openswarm only, updated: 2026-04-22) -->

# DECISIONS [OSW]

> **Related:** [ORCHESTRATOR_REDESIGN](ORCHESTRATOR_REDESIGN.md) · [SPEC_V2](SPEC_V2.md) · [DASHBOARD DECISIONS](dashboard/DECISIONS.md)

ADR-001 [OSW]: opencode REST API as Session Backend (2026-04-22, Accepted)
ADR-002 [OSW]: Direct Orch Session Lookup — Hermes Layer Removed (2026-04-22, Accepted)
ADR-003 [OSW]: SSE Proxy for Live Activity Indicators (2026-04-22, Accepted)
ADR-004 [OSW]: Abort Button via opencode REST (2026-04-22, Accepted)
ADR-005 [OSW]: Fork Session via opencode REST (2026-04-22, Accepted)
ADR-006 [OSW]: Revert via opencode REST (2026-04-22, Accepted)
ADR-007 [OSW]: Debug Endpoint for Orch Diagnostics (2026-04-22, Accepted)
ADR-008 [OSW]: Learning Orchestrator via Persistent File Memory (2026-04-22, Proposed — Future)

---

### ADR-008 [OSW]: Learning Orchestrator via Persistent File Memory (Future)

- **Date**: 2026-04-22
- **Status**: Proposed — not yet needed; revisit when cross-run learning becomes a bottleneck
- **Area**: orchestrator.py / prompts

#### Context

Exploration of Letta Code as an alternative agent backend (2026-04-22) concluded:

- Letta Code is infeasible as a **worker** replacement (no headless `run` mode equivalent to `opencode run`).
- Letta Code as an **orchestrator** would replicate the Hermes failure: an always-on LLM orchestrator adds indirection without eliminating the underlying control loop, and manual deterministic control is preferred (see ADR-002).
- Letta's one genuine differentiator is **cross-session learning**: persistent memory blocks and skill accumulation that allow an agent to refine prompts and retry strategies over time — something `state.json` does not currently provide.

This capability is not needed today. Worker prompts are manually maintained and failure patterns are handled by the investigator/retry loop. However, if task failure rates remain high or prompt maintenance becomes a bottleneck, a file-based learning layer would be the right approach — without adopting Letta or any always-on agent infrastructure.

#### Proposed Decision

When cross-run learning is needed, implement it as explicit file-based persistent memory within the existing architecture:

1. **`prompts/orch-memory.md`** — a human-readable, git-tracked file accumulating patterns across runs (known failure causes, prompt refinements, per-repo notes). Injected into worker prompts by `build_worker_prompt()`.

2. **Post-run synthesis session** — after `run_orchestrator()` completes, an `opencode run` call with a "synthesise lessons" prompt reads `reports/` + historical `state.json` and outputs updates to `orch-memory.md`. This replicates Letta's reflection subagent explicitly.

3. **Structured failure tracking** — a `failures.json` (or section of `orch-memory.md`) accumulating `failure_reason` + `investigator_diagnosis` across runs, keyed by task type/repo. Used to inject targeted context for known-problematic task categories.

4. **Template selection** (optional later step) — multiple worker prompt templates selectable based on task tags or failure history, replacing the single `worker-template.md`.

#### Why not Letta Code

Letta's memory update is more elegant (the agent self-writes during execution), but the only practical gap vs. the file-based approach is **intra-run memory update** — memory updating before the session ends rather than after. For an orchestrator managing discrete tasks with clear start/end points, this is immaterial.

The file-based approach is preferable here because:
- Memory is plain Markdown/JSON — version-controlled, auditable, editable by hand.
- No additional infrastructure (no Letta server, no DB).
- Fits the existing deterministic architecture; no LLM in the control loop.

#### Trigger for revisiting

Implement when any of the following are true:
- A task type fails on first attempt in >50% of runs.
- Prompt maintenance (updating `worker-template.md`, `AGENTS.md`) becomes a recurring manual task between runs.
- The investigator produces repeated identical diagnoses across multiple runs for the same task category.

#### Consequences (when implemented)

- `build_worker_prompt()` gains an additional injection from `orch-memory.md`.
- A post-run synthesis step is added to `run_orchestrator()` or called from `swarm.sh`.
- `orch-memory.md` is committed to git — it is human-editable curated knowledge, not machine run-state.

---

### ADR-007 [OSW]: Debug Endpoint for Orch Diagnostics

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / diagnostics

#### Context

During the hermes-to-opencode migration it was difficult to verify data integrity
without browser access — there was no way to inspect what sessions opencode was
returning versus what `projects.json` contained.

#### Decision

Add `GET /api/debug` to `server.py`. Returns a JSON object with:

- `projects_json` — raw contents of `projects.json`
- `oc_all_sessions_count` — total sessions known to opencode
- `oc_orch_sessions` — sessions whose directory matches the orch worktree
- `statuses` — per-task status summary from `state.json`

No authentication; intended for local curl use only.

#### Consequences

- Provides a single curl call to confirm end-to-end data flow without a browser.
- No external exposure (dashboard binds to localhost only).

---

### ADR-006 [OSW]: Revert via opencode REST

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / session ops

#### Context

Undo (revert) operations were previously implemented as raw SQLite DELETE statements
against opencode's internal database. This was fragile and tied to opencode's storage
schema, which is not a public interface.

#### Decision

Replace raw SQLite DELETE with `POST /api/oc/session/<id>/revert`, which proxies to
the opencode REST endpoint `POST http://localhost:4097/session/{id}/revert`.

#### Consequences

- No longer coupled to opencode's internal SQLite schema.
- Revert behaviour is governed by opencode, ensuring consistency with its own undo logic.

---

### ADR-005 [OSW]: Fork Session via opencode REST

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / dashboard.html

#### Context

The dashboard needed the ability to branch an orchestrator session at a given message
in order to explore alternative instruction paths without losing the original session.

#### Decision

Add `POST /api/oc/session/<id>/fork` to `server.py`, proxying to
`POST http://localhost:4097/session/{id}/fork`. Returns the new Session object.

In `dashboard.html`, `forkMsg()` calls this endpoint, then immediately sends the
follow-up message to the new session and switches the orch panel to it.

#### Consequences

- Fork creates a new session in opencode; the original session is unaffected.
- The dashboard automatically navigates to the fork after creation.

---

### ADR-004 [OSW]: Abort Button via opencode REST

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / dashboard.html

#### Context

There was no way to interrupt a running opencode session from the dashboard. Long or
misdirected prompts had to be left to run to completion.

#### Decision

Add `POST /api/oc/session/<id>/abort` to `server.py`, proxying to
`POST http://localhost:4097/session/{id}/abort`.

In `dashboard.html`, an activity bar (`.activity-bar`) is rendered above the chat
textarea. It shows an animated pulse dot and an abort button whenever the session is
active. The bar is hidden when the session is idle.

#### Consequences

- Users can cancel a running session without restarting the service.
- Activity state is driven by SSE events (see ADR-003); the abort button is only
  visible when activity is detected.

---

### ADR-003 [OSW]: SSE Proxy for Live Activity Indicators

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / dashboard.html

#### Context

The dashboard had no real-time visibility into whether an opencode session was
actively executing a tool or waiting for input. Polling message lists was too coarse
to show sub-second activity.

#### Decision

Add `GET /api/stream/oc-events/<sessionId>` to `server.py`. This endpoint proxies
`GET http://localhost:4097/global/event` (opencode's SSE stream) and filters events
by session ID. Events of type `message.part.updated` with `part.type=tool` and
`state.status=running` are forwarded to the browser as activity updates, surfacing
the tool description in an activity pill. A `session.idle` event clears the pill.

#### Consequences

- Dashboard shows live per-session activity without polling.
- The SSE connection is per-session; navigating away closes the stream.
- Requires opencode's global event stream to be stable and filterable by session ID.

---

### ADR-002 [OSW]: Direct Orch Session Lookup — Hermes Layer Removed

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py / dashboard.html

#### Context

The orchestrator panel previously used `projects.json` (a legacy "hermes" layer) to
track orch projects. Loading the panel required a two-step lookup:

1. Parse `projects.json` → find the orch project entry
2. Read `<session.dir>/.orch-session` → resolve the opencode session ID

This introduced a race condition: if the panel opened before `loadOrchProjects()`
resolved, `activeOrchProjectId` was null and the panel displayed "No session
selected". More fundamentally, hermes was deprecated for two reasons:

1. **Session interaction**: getting clean session read/write through the hermes layer
   was significantly harder than using opencode's REST API directly. opencode exposed
   the endpoints needed; hermes did not.

2. **Hermes' core value proposition collapsed**: hermes was conceived as an always-on
   state-managing agent. In practice, the autonomous mode still required a cron script
   regardless — hermes did not eliminate that need. Combined with a shift toward
   wanting to interact with the orchestrator manually rather than autonomously, the
   "always-on agent" benefit became redundant before it was ever relied upon.

#### Decision

Add `GET /api/orch/session` to `server.py`. This endpoint queries opencode directly
via `GET http://localhost:4097/session?directory=<ORCH_DIR>` and returns the most
recently updated matching session, where `ORCH_DIR` is the path containing the
`.orch-session` marker file.

In `dashboard.html`, `refreshOrchSession()` calls `/api/orch/session` directly.
The variables `orchProjectList`, `activeOrchProjectId`, and all hermes-layer
indirection are removed. `loadOrchProjects`, `renderSessionTabs`, `removeSession`,
`selectOrchProject`, and `spawnSession` are stubbed or simplified to no-ops.

#### Why the hermes layer existed

`projects.json` predated the opencode REST API. When the only interface to opencode
was the CLI, a separate tracking file was needed to map project directories to session
IDs. Once opencode exposed `GET /session?directory=…`, the tracking file became
redundant. The shift to manual orchestrator interaction removed the last reason to
keep hermes in the loop.

#### Consequences

- No race condition: the session is fetched fresh on every panel open.
- `projects.json` is no longer written or read by the dashboard; it remains on disk
  as a legacy artefact but is ignored.
- The orch session is identified solely by its opencode session ID
  (currently `ses_24db37a5fffeFutTYGWLCjzsxj` at `/home/ubuntu/server/openswarm/.orch-session`).

---

### ADR-001 [OSW]: opencode REST API as Session Backend

- **Date**: 2026-04-22
- **Status**: Accepted
- **Area**: server.py

#### Context

`server.py` originally interacted with opencode through a mix of direct SQLite reads
against opencode's internal database and `opencode run` subprocess calls. Both
approaches were fragile: SQLite reads depended on internal schema details, and
subprocess calls were slow and stateless.

#### Decision

All session operations in `server.py` now go through the opencode REST API running
on `http://localhost:4097`:

| Operation | Endpoint |
|---|---|
| List sessions | `GET /session` |
| Get messages | `GET /session/{id}/message` |
| Send message (async) | `POST /session/{id}/prompt_async` |
| Abort session | `POST /session/{id}/abort` |
| Fork session | `POST /session/{id}/fork` |
| Revert session | `POST /session/{id}/revert` |
| Live events | `GET /global/event` (SSE) |

Message objects returned by opencode follow the shape
`{info: {id, role, …}, parts: […]}`. `server.py` normalises these by hoisting
`info.id` to the top-level `msg.id` before forwarding to the dashboard.

#### Consequences

- No SQLite dependency; schema changes in opencode do not break the dashboard.
- opencode must be running on port 4097 for the dashboard to function.
- All session mutations are serialised through opencode, ensuring consistency with
  its own session model.
