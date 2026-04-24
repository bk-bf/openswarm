<!-- LOC cap: 300 (created: 2026-04-24) -->

# ARCHITECTURE [DASHBOARD]

> **Related:** [DECISIONS](DECISIONS.md) · [DESIGN](DESIGN.md) · [PHILOSOPHY](PHILOSOPHY.md) · [BUGS](../bugs/BUGS.md) · [ROADMAP](features/open/ROADMAP.md)

## Runtime Shape

- Svelte 5 + SvelteKit frontend, `@sveltejs/adapter-static` SPA build
- Build output: `openswarm/dashboard/build/` — served verbatim by `server.py`
- No Node runtime in production. `server.py` remains the sole backend process
  and continues to own: OC SSE proxying, task state, orchestrator control,
  investigator logs, the `openswarm-dashboard.service` systemd unit
- Dev: `pnpm dev` on port 5174 with Vite proxy to `server.py` on 7700
- Prod: `pnpm build` → `server.py` serves `dashboard/build/index.html` as SPA
  fallback plus static assets

## Source Tree

```
openswarm/dashboard/
├── package.json
├── svelte.config.js
├── vite.config.ts
├── tsconfig.json
├── knip.json
├── check.sh
└── src/
    ├── app.html
    ├── app.css                         — verbatim port of dashboard.html <style>
    ├── app.d.ts
    ├── hooks.client.ts
    ├── lib/
    │   ├── api/
    │   │   ├── oc.ts                   — @opencode-ai/sdk client pointed at server.py proxy
    │   │   └── swarm.ts                — hand-typed client for server.py native endpoints
    │   ├── sync/                       — minimal openchamber-pattern sync layer
    │   │   ├── types.ts
    │   │   ├── event-pipeline.ts       — SSE transport, batch flush, heartbeat, reconnect
    │   │   ├── event-reducer.ts        — apply events to in-memory state
    │   │   ├── reconnect-recovery.ts   — fetch missed events on reconnect
    │   │   ├── streaming.ts            — token-by-token merge for text/reasoning
    │   │   └── sync-context.svelte.ts  — rune-based context creator
    │   ├── composables/                — rune-based hooks (.svelte.ts)
    │   │   ├── useOCSync.svelte.ts
    │   │   ├── useSession.svelte.ts
    │   │   ├── useMessages.svelte.ts
    │   │   ├── usePermissions.svelte.ts
    │   │   └── useTasks.svelte.ts
    │   ├── components/
    │   │   ├── LoadingDots.svelte
    │   │   ├── StatusChip.svelte
    │   │   ├── ModelPicker.svelte
    │   │   ├── AgentPicker.svelte
    │   │   └── parts/                  — one component per OC part type
    │   │       ├── PartRenderer.svelte
    │   │       ├── TextPart.svelte
    │   │       ├── ReasoningPart.svelte
    │   │       ├── ToolPart.svelte
    │   │       ├── StepMarkerPart.svelte
    │   │       ├── PatchPart.svelte
    │   │       ├── FilePart.svelte
    │   │       ├── SnapshotPart.svelte
    │   │       ├── TodoPart.svelte
    │   │       ├── AgentPart.svelte
    │   │       ├── CompactionPart.svelte
    │   │       └── RetryPart.svelte
    │   ├── layouts/
    │   │   └── AppShellLayout.svelte   — nav + settings + orch panel shell
    │   ├── pages/                      — feature orchestrators; routes only wire
    │   │   ├── dashboard/
    │   │   │   ├── DashboardView.svelte
    │   │   │   ├── TaskGraph.svelte
    │   │   │   ├── WorkerCard.svelte
    │   │   │   ├── WorkerCardList.svelte
    │   │   │   └── OrchPanel.svelte
    │   │   ├── session/
    │   │   │   ├── SessionView.svelte
    │   │   │   ├── MessageList.svelte
    │   │   │   ├── MessageRow.svelte
    │   │   │   ├── Composer.svelte
    │   │   │   └── PermissionPrompt.svelte
    │   │   └── settings/
    │   │       └── SettingsDrawer.svelte
    │   ├── utils/
    │   │   └── formatters.ts
    │   └── types/
    │       └── index.ts
    └── routes/
        ├── +layout.svelte              — mounts shell + provides contexts
        └── +page.svelte                — mounts DashboardView
```

## Data Flow

1. `+layout.svelte` creates the sync context via `createSyncContext()` and provides
   it with `setContext(SYNC_KEY, ctx)`. The context spawns one `EventPipeline`
   that owns two SSE connections:
   - `/api/oc/event` — OpenCode global events (proxied by `server.py`)
   - `/api/state/stream` — OpenSwarm task state deltas (new endpoint in `server.py`)
2. The pipeline batches events on a 33 ms flush frame, forwards them through
   `event-reducer.ts`, which mutates reactive state (`$state` runes) held inside
   the context.
3. Composables (`useSession`, `useMessages`, `usePermissions`, `useTasks`) read
   the context and expose focused derivations via `$derived`.
4. Components consume composables, never the raw context.
5. Actions (send message, approve/deny permission, retry task) call functions on
   `lib/api/*.ts`, which hit `server.py` endpoints directly.

## State Ownership Map

| State | Owner | Lifetime |
|---|---|---|
| OC session list, messages, parts | `sync-context.svelte.ts` (in-memory rune state) | Page session |
| Pending permissions | `sync-context.svelte.ts` | Until grant/reject |
| OpenSwarm task graph, statuses | `sync-context.svelte.ts` | Page session |
| UI preferences (panel sizes, open tabs) | `lib/composables/useUIPrefs.svelte.ts` + `localStorage` | Persisted |
| Per-session composer draft | `lib/composables/useSession.svelte.ts` per session id | Page session |
| Permission flags (auto-approve defaults) | `lib/composables/useUIPrefs.svelte.ts` + `localStorage` | Persisted |

## API Surface

All endpoints served by `server.py` on port 7700.

### Existing (preserved)
- `GET  /api/oc/session` — list OC sessions (proxy)
- `POST /api/oc/session/:id/message` — send message (proxy)
- `GET  /api/oc/session/:id/message` — fetch message history (proxy)
- `GET  /api/oc/event` — OC SSE event stream (proxy)
- `POST /api/oc/permission/:id/:action` — grant/reject (proxy)
- `GET  /api/state` — OpenSwarm task state snapshot
- `GET  /api/settings` — dashboard settings
- `POST /api/orchestrator/*` — orchestrator controls

### To add in Phase 2
- `GET /api/state/stream` — SSE stream of task state deltas
  - Emits `task.updated`, `task.started`, `task.finished`, `orchestrator.status`

## Build & Deployment

```bash
# Development
cd dashboard && pnpm install && pnpm dev             # 5174, HMR
python3 ../server.py --dev                           # 7700, proxies to 5174 for dashboard routes

# Production
cd dashboard && pnpm install && pnpm build           # → dashboard/build/
python3 server.py                                    # serves dashboard/build/ at /
```

Systemd: `openswarm-dashboard.service` keeps running `server.py` on 7700.
The build step is a prerequisite; there is no auto-rebuild on deploy.

## Check Pipeline

`dashboard/check.sh` mirrors `yact-web/scripts/check.sh`:

```bash
pnpm svelte-check --threshold error
pnpm knip
pnpm vitest run --passWithNoTests
```

Required before any commit that touches `dashboard/`. Added to
`openswarm/AGENTS.md` verification block at Phase 5 cutover.

## What Lives Where (quick reference)

| Concern | File / Dir |
|---|---|
| "How do events arrive and get stored?" | `lib/sync/` |
| "How does a component read live session data?" | `lib/composables/use*.svelte.ts` |
| "Where do I render a new OC part type?" | `lib/components/parts/` |
| "Where does a page assemble its sub-views?" | `lib/pages/<feature>/` |
| "How does the dashboard talk to OC?" | `lib/api/oc.ts` (via `server.py` proxy) |
| "How does the dashboard talk to OpenSwarm?" | `lib/api/swarm.ts` (direct `server.py` endpoints) |
| "Where do global CSS variables live?" | `src/app.css` (verbatim from legacy dashboard) |
| "Where is the router wired?" | `src/routes/+layout.svelte`, `+page.svelte` — wiring only |
