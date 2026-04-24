<!-- LOC cap: 400 (created: 2026-04-24) -->

# ROADMAP [DASHBOARD MIGRATION]

> **Related:** [ARCHITECTURE](../../ARCHITECTURE.md) · [DECISIONS](../../DECISIONS.md) · [DESIGN](../../DESIGN.md) · [PHILOSOPHY](../../PHILOSOPHY.md)

Single source of truth for the Svelte migration. Each phase has an exit
criterion that must pass before moving on.

## Status Summary

| Phase | Status | LoC budget | Est. days |
|---|---|---|---|
| 0  Worktree backup | ✅ done 2026-04-24 | — | 5 min |
| 0.5 Spec docs | ✅ done 2026-04-24 | ~1500 docs | 0.5 |
| 1  Scaffold SvelteKit | ⏳ pending | ~200 | 0.5 |
| 2  Sync layer | ⏳ pending | ~900 | 1-2 |
| 3  Part renderers | ⏳ pending | ~1100 | 1-2 |
| 4  UI port | ⏳ pending | ~3200 | 2-3 |
| 5  Cutover | ⏳ pending | ~100 | 0.5 |
| **Total** | | **~5500 LoC** | **5-8 days** |

---

## Phase 0 — Worktree backup ✅

Completed 2026-04-24.

- 8 staged commits (`5cf5b96` … `1d14104`) consolidate the WIP that existed
  on `main` before migration work began.
- Detached-HEAD backup at `/home/agent/openswarm-pre-svelte/` points at
  commit `1d14104`.
- `.gitignore` now excludes `features/` (worktrees) and `orch-session.id`
  (runtime file).

---

## Phase 0.5 — Spec docs ✅

Completed 2026-04-24. All `.docs/dashboard/` files written and committed
before any code. Dashboard-scoped bugs fold into the repo-root
`.docs/bugs/BUGS.md` rather than a duplicate nested file.

Files:

- [x] `.docs/dashboard/ARCHITECTURE.md`
- [x] `.docs/dashboard/DESIGN.md`
- [x] `.docs/dashboard/PHILOSOPHY.md`
- [x] `.docs/dashboard/DECISIONS.md`
- [x] `.docs/dashboard/features/open/ROADMAP.md` (this file)
- [x] `.docs/dashboard/features/open/CSS-REFACTOR.md`
- [x] `.docs/dashboard/features/open/SYNC-EXTENSIONS.md`

**Exit criteria**: user reviews and approves before Phase 1 begins. ✅

---

## Phase 1 — Scaffold SvelteKit

### Commands

```bash
cd /home/ubuntu/server/openswarm
pnpm create svelte@latest dashboard   # Svelte 5, TS strict, vitest, ESLint, Prettier, no Tailwind
cd dashboard
pnpm add -D @sveltejs/adapter-static svelte-check knip
pnpm add @opencode-ai/sdk marked shiki
```

### Files

- `dashboard/package.json` — name `@openswarm/dashboard`, scripts mirror yact-web
- `dashboard/svelte.config.js` — `adapter-static` with `fallback: 'index.html'`
- `dashboard/vite.config.ts` — dev proxy `/api/*` → `http://localhost:7700`
- `dashboard/tsconfig.json` — strict, extends `.svelte-kit/tsconfig.json`
- `dashboard/knip.json` — base config (adapt from yact-web)
- `dashboard/check.sh` — svelte-check → knip → vitest
- `dashboard/src/app.html`, `app.css` (empty stub at Phase 1), `app.d.ts`
- `dashboard/src/lib/` scaffolding (empty dirs with `.gitkeep`)
- `dashboard/src/lib/components/LoadingDots.svelte` — ported verbatim from
  `yact-web/apps/web/src/lib/components/LoadingDots.svelte`
- `dashboard/src/routes/+layout.svelte`, `+page.svelte` — placeholder
- `openswarm/.gitignore` — add `dashboard/node_modules/`, `dashboard/.svelte-kit/`,
  `dashboard/build/`
- `server.py` — add `--new-dashboard` flag that serves `dashboard/build/`
  instead of `dashboard.html` when set

### Exit criteria

1. `pnpm build` in `dashboard/` succeeds and produces `build/index.html`.
2. `python3 server.py --new-dashboard` serves the placeholder page showing
   "OpenSwarm" and a `<LoadingDots />` at `http://localhost:7700/`.
3. `python3 server.py` (no flag) still serves the legacy `dashboard.html`.
4. `dashboard/check.sh` exits 0 (no type errors, no knip hits, zero tests pass).

### Commit shape

- `dashboard: scaffold sveltekit app with adapter-static`
- `dashboard: add LoadingDots component ported from yact-web`
- `dashboard: add check.sh pipeline (svelte-check, knip, vitest)`
- `server.py: add --new-dashboard flag behind which to serve dashboard/build/`

---

## Phase 2 — Sync layer

### Files

| File | LoC budget | Purpose |
|---|---|---|
| `lib/sync/types.ts` | ~150 | Re-export SDK types + OpenSwarm event shapes |
| `lib/sync/event-pipeline.ts` | ~350 | SSE transport, batch flush (33 ms), heartbeat (30 s), reconnect (exponential, capped at 5 s) |
| `lib/sync/event-reducer.ts` | ~200 | Pure fn: `(state, event) → state'` for all OC + swarm events |
| `lib/sync/reconnect-recovery.ts` | ~80 | On reconnect within 60 s window, fetch missed events |
| `lib/sync/streaming.ts` | ~100 | Token-by-token merge for text/reasoning parts |
| `lib/sync/sync-context.svelte.ts` | ~150 | Rune-based context creator wrapping the above |
| `lib/composables/useOCSync.svelte.ts` | ~50 | Top-level context accessor |
| `lib/composables/useSession.svelte.ts` | ~80 | Per-session state slice |
| `lib/composables/useMessages.svelte.ts` | ~80 | Message list derivations |
| `lib/composables/usePermissions.svelte.ts` | ~80 | Pending permissions + grant/reject actions |
| `lib/composables/useTasks.svelte.ts` | ~80 | OpenSwarm task graph state |
| `lib/api/oc.ts` | ~80 | SDK client pointed at `server.py` proxy |
| `lib/api/swarm.ts` | ~100 | Hand-typed client for native `server.py` endpoints |

### Backend additions to `server.py`

- `GET /api/state/stream` — SSE stream emitting `task.updated`,
  `task.started`, `task.finished`, `orchestrator.status`. Replaces the current
  400 ms polling of `/api/state`.

### Exit criteria

1. Vitest unit tests for `event-reducer.ts` pass against canned SSE frame
   fixtures covering every OC event type.
2. Integration test: start a dummy OC session via `server.py`; verify events
   stream through the reducer and mutate `state.sessions[<id>].messages`.
3. Integration test: force-disconnect the SSE mid-stream; verify
   `reconnect-recovery.ts` fetches and applies missed events.
4. No UI work yet. Dev page renders `<pre>{JSON.stringify(ctx.state, null, 2)}</pre>`.

### Commit shape

Small commits per file group:
- `dashboard: add sync types and api clients`
- `dashboard: add event-pipeline with reconnect and batching`
- `dashboard: add event-reducer with full OC event coverage`
- `dashboard: add reconnect recovery and streaming merge`
- `dashboard: add sync-context and composables`
- `server.py: add /api/state/stream SSE endpoint`

---

## Phase 3 — Part renderers

### Files (all under `lib/components/parts/`)

| Component | LoC | Handles |
|---|---|---|
| `PartRenderer.svelte` | ~60 | Discriminator dispatch |
| `TextPart.svelte` | ~80 | Markdown via `marked` + code blocks via `shiki` |
| `ReasoningPart.svelte` | ~60 | Collapsible |
| `ToolPart.svelte` | ~200 | Generic tool part container |
| `ToolPart.<tool>.svelte` | ~20 × 9 | Per-tool renderers: bash, read, edit, write, grep, glob, webfetch, task, todowrite |
| `StepMarkerPart.svelte` | ~40 | `step-start`, `step-finish` |
| `PatchPart.svelte` | ~200 | Shiki-based diff rendering via `diff` npm package |
| `FilePart.svelte` | ~40 | Attached file reference |
| `SnapshotPart.svelte` | ~60 | Snapshot marker |
| `TodoPart.svelte` | ~80 | Todo list rendering |
| `AgentPart.svelte` | ~50 | Subagent marker |
| `CompactionPart.svelte` | ~40 | Context compaction marker |
| `RetryPart.svelte` | ~40 | Retry marker (already in legacy) |

### Test fixtures

Capture SSE traces from 3-5 real completed runs under `openswarm/logs/*.log`.
Store fixtures at `dashboard/src/lib/components/parts/__fixtures__/`. Snapshot
test `PartRenderer` against each.

### Exit criteria

1. Every part type captured in fixtures renders without the "unknown part
   type" fallback.
2. Vitest snapshot suite green.
3. No regressions in `check.sh`.

### Commit shape

- `dashboard: add PartRenderer dispatcher and text/reasoning parts`
- `dashboard: add tool part with per-tool renderers`
- `dashboard: add patch/file/snapshot parts`
- `dashboard: add todo/agent/compaction/retry/step parts`
- `dashboard: add parts fixture snapshot tests`

---

## Phase 4 — UI port

Goal: visual parity with legacy `dashboard.html`. Same CSS, same layout, same
features — just componentised and type-safe.

### Files

| File | LoC | Legacy source |
|---|---|---|
| `src/app.css` | ~2500 | Lifted verbatim from `dashboard.html` `<style>` |
| `lib/layouts/AppShellLayout.svelte` | ~200 | Top nav, settings button, orch panel toggle |
| `lib/pages/dashboard/DashboardView.svelte` | ~150 | Orchestrator (wiring only) |
| `lib/pages/dashboard/TaskGraph.svelte` | ~250 | DAG rendering |
| `lib/pages/dashboard/WorkerCard.svelte` | ~150 | Per-task card |
| `lib/pages/dashboard/WorkerCardList.svelte` | ~80 | Grid wrapper |
| `lib/pages/dashboard/OrchPanel.svelte` | ~150 | Orchestrator session embed |
| `lib/pages/session/SessionView.svelte` | ~150 | Session orchestrator |
| `lib/pages/session/MessageList.svelte` | ~200 | Part-bearing message list |
| `lib/pages/session/MessageRow.svelte` | ~80 | Single message frame |
| `lib/pages/session/Composer.svelte` | ~250 | Input + model/agent selectors + send |
| `lib/pages/session/PermissionPrompt.svelte` | ~150 | Approve/deny card |
| `lib/pages/settings/SettingsDrawer.svelte` | ~200 | Settings UI |
| `lib/components/StatusChip.svelte` | ~40 | Status pill |
| `lib/components/ModelPicker.svelte` | ~80 | Model dropdown |
| `lib/components/AgentPicker.svelte` | ~60 | Agent dropdown |
| `src/routes/+layout.svelte` | ~30 | Provides contexts, mounts shell |
| `src/routes/+page.svelte` | ~10 | Mounts DashboardView |

### Exit criteria

1. Side-by-side visual comparison against `/home/agent/openswarm-pre-svelte`
   dashboard shows pixel-close parity on the main views.
2. All features from Phase 2 (live sync) and Phase 3 (part coverage) active.
3. Permissions are actionable (grant/reject buttons work end-to-end).
4. `--dry-run` smoke test passes against the new dashboard.

### Commit shape

Commit per feature area:
- `dashboard: port app.css verbatim from dashboard.html`
- `dashboard: add AppShellLayout and routes wiring`
- `dashboard: port task graph and worker cards view`
- `dashboard: port session view and message list`
- `dashboard: port composer with model/agent pickers`
- `dashboard: add permission prompt card`
- `dashboard: port orchestrator panel`
- `dashboard: port settings drawer`

---

## Phase 5 — Cutover

### Changes

1. `server.py`: remove `--new-dashboard` flag; default to serving
   `dashboard/build/`.
2. Delete `dashboard.html` (backup worktree preserves it).
3. `openswarm/AGENTS.md`:
   - Remove `dashboard.html` from layout table.
   - Add `dashboard/` entry.
   - Add `cd dashboard && ./check.sh` to the verification block when dashboard
     files are touched.
   - Note `/home/agent/openswarm-pre-svelte/` as rollback target (remove entry
     after 2 weeks of stability).

### Exit criteria

1. `systemctl restart openswarm-dashboard` serves the new dashboard with no
   legacy flag.
2. `./swarm.sh --dry-run` renders cleanly end-to-end.
3. A real small task (one worker) runs to completion with all parts visible
   and permissions actionable.

### Commit shape

- `server.py: default to serving dashboard/build; remove --new-dashboard flag`
- `dashboard: remove legacy dashboard.html`
- `AGENTS.md: update for svelte dashboard; document rollback worktree`

---

## Phase 6 — Post-cutover (out of scope for this roadmap)

Listed for completeness; separate roadmaps when / if started:

- Decompose `app.css` per component (see `features/open/CSS-REFACTOR.md`)
- Evaluate deferred sync features (see `features/open/SYNC-EXTENSIONS.md`)
- Remove `/home/agent/openswarm-pre-svelte/` worktree after 2 weeks of prod use
- Consider migrating select `server.py` endpoints to SvelteKit `routes/api/*`
  (only if a typed BFF layer proves valuable)
