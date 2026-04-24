<!-- LOC cap: 400 (created: 2026-04-24) -->

# DECISIONS [DASHBOARD]

> **Related:** [ARCHITECTURE](ARCHITECTURE.md) · [DESIGN](DESIGN.md) · [PHILOSOPHY](PHILOSOPHY.md) · [ROADMAP](features/open/ROADMAP.md)

ADR-001 [DASHBOARD]: Migrate From Monolithic dashboard.html To SvelteKit (2026-04-24, Accepted)
ADR-002 [DASHBOARD]: Use adapter-static; server.py Continues To Serve Frontend (2026-04-24, Accepted)
ADR-003 [DASHBOARD]: Adopt yact-web Conventions For Directory Structure And Patterns (2026-04-24, Accepted)
ADR-004 [DASHBOARD]: Minimal Sync Layer Subset From Openchamber, Add On Demand (2026-04-24, Accepted)
ADR-005 [DASHBOARD]: Full OC Part-Type Coverage Required At First Cutover (2026-04-24, Accepted)
ADR-006 [DASHBOARD]: Copy Legacy CSS Verbatim; Defer Component-Scoped Refactor (2026-04-24, Accepted)
ADR-007 [DASHBOARD]: Use @opencode-ai/sdk For Both Types And Transport (2026-04-24, Accepted)
ADR-008 [DASHBOARD]: Commit Migration Work Directly To main With Staged Commits (2026-04-24, Accepted)

---

### ADR-001 [DASHBOARD]: Migrate From Monolithic dashboard.html To SvelteKit

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Frontend architecture

#### Context

`dashboard.html` has grown to ~6000 effective lines of HTML+CSS+inline JS with
no modules, no TypeScript, no tests. It silently drops several OC part types,
ignores permission events (causing workers to hang), and uses full-DOM
`innerHTML` replacement for every event. Adding any feature requires reading
the whole file to avoid collisions.

#### Decision

Rewrite the dashboard as a Svelte 5 + SvelteKit SPA with TypeScript, vitest,
and the check pipeline from yact-web. Keep the existing `server.py` backend
unchanged except for one new SSE endpoint.

#### Rationale

Svelte 5 runes map cleanly to the "event arrives → update state → re-render"
pattern of a chat-like dashboard. SvelteKit provides filesystem routing,
`+page.ts` loaders, and typed route params without writing glue code. The
project already has a mature SvelteKit reference at `yact-web` — conventions
can be lifted rather than invented.

Alternatives considered and rejected:

- **Plain Svelte bundle (no Kit)**: would mean re-inventing Kit's routing and
  build conventions, diverging from the yact-web patterns we want to reuse.
- **React 19 with openchamber's packages/ui vendored**: inherits 180k LoC of
  someone else's opinions; drops the "small, surgical codebase" goal.
- **SolidJS matching opencode/app**: ergonomically similar to React; no
  existing in-house SolidJS reference to copy patterns from.

---

### ADR-002 [DASHBOARD]: Use adapter-static; server.py Continues To Serve Frontend

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Deployment topology

#### Context

SvelteKit supports multiple adapters. `adapter-node` would run SvelteKit as a
Node process and enable typed BFF routes under `routes/api/*`. `adapter-static`
emits a pure static bundle.

#### Decision

Use `@sveltejs/adapter-static` with SPA fallback (`fallback: 'index.html'`).
`server.py` serves `dashboard/build/` as static files and continues to own all
backend concerns, including the OpenCode SSE proxy.

#### Rationale

`server.py` is already the BFF: it proxies `/api/oc/*` to OpenCode, reads
`state.json`, exposes orchestrator control endpoints. Running a second Node
process just to provide `routes/api/*` type-safety on top of the same endpoints
is duplication with no functional gain. The existing systemd unit
(`openswarm-dashboard.service`) stays as-is.

Trade-off accepted: `routes/api/*` in SvelteKit is unused at runtime. The
pattern is not forbidden for future use (e.g. dev-only mocks), but no
production route goes through it.

---

### ADR-003 [DASHBOARD]: Adopt yact-web Conventions For Directory Structure And Patterns

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Conventions

#### Context

`yact-web` is the most mature in-house SvelteKit project. Its conventions are
documented in `yact-dev-docs/web/` and have been stress-tested by ongoing
feature work.

#### Decision

Lift the following yact-web conventions verbatim:

- `lib/components/` for leaf UI primitives.
- `lib/composables/*.svelte.ts` for rune-based hooks.
- `lib/effects/*.svelte.ts` for reusable rune effects.
- `lib/layouts/` for top-level page shells.
- `lib/pages/<feature>/` for feature view orchestrators.
- `lib/utils/`, `lib/types/`.
- `routes/` holds wiring only; orchestration lives in `lib/pages/`.
- `.svelte.ts` extension for all rune-containing TS.
- `LoadingDots.svelte` as the universal async-wait placeholder.
- Context-based global state via `setContext` / `getContext` with exported
  symbol keys.
- `svelte-check → knip → vitest` check pipeline.
- `knip.json` for dead-code detection.

#### Rationale

Re-using a known-good structure means less bikeshedding, faster onboarding for
agents already familiar with yact-web, and predictable LSP behaviour. Where
yact-web's docs describe a pattern, this repo's docs reference rather than
duplicate.

---

### ADR-004 [DASHBOARD]: Minimal Sync Layer Subset From Openchamber, Add On Demand

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Sync layer

#### Context

Openchamber's `packages/ui/src/sync/` is ~4500 LoC across 30+ files including
optimistic writes, content cache, LRU eviction, prefetch cache, binary blob
handling, multi-tab sync, persist cache. For a single-user local-host
deployment, many of these features add complexity with no user-visible
benefit.

#### Decision

Port only the following patterns at Phase 2:

- `event-pipeline.ts` — SSE transport with batch flush, heartbeat, reconnect
- `event-reducer.ts` — pure function mapping events to state mutations
- `reconnect-recovery.ts` — fetch missed events inside the reconnect window
- `streaming.ts` — token-by-token merging for text/reasoning parts
- `sync-context.svelte.ts` — rune-based context creator wrapping the above
- `types.ts` — wrapping `@opencode-ai/sdk` types plus OpenSwarm-local events

Explicitly deferred (add only if a concrete need surfaces):

- `optimistic.ts` (UX round-trip is <10 ms on localhost)
- `retry.ts` (fetch retries live in `lib/api/*`)
- `session-cache.ts` beyond in-memory
- `content-cache.ts` (no inline file-content rendering yet)
- `persist-cache.ts` (page refresh is cheap)

Explicitly rejected:

- `eviction.ts` (memory pressure is a non-concern)
- `session-prefetch-cache.ts` (no multi-session navigation)
- `binary.ts` (OpenSwarm workers don't produce binary parts)

#### Rationale

See PHILOSOPHY § "Every feature must earn its LoC". The deferred items are
logged in `features/open/SYNC-EXTENSIONS.md` with the trigger condition that
would promote them to in-scope.

---

### ADR-005 [DASHBOARD]: Full OC Part-Type Coverage Required At First Cutover

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Feature scope

#### Context

The legacy `dashboard.html` renders only `text`, `reasoning`, `tool`, and
`retry` part types. Other OC part types (`step-start`, `step-finish`, `patch`,
`file`, `snapshot`, `todo`, `agent`, `compaction`) are silently dropped,
leaving gaps in what the dashboard shows during a run. Fixing this is the
primary motivation for the migration.

#### Decision

All OC part types must render before cutover (Phase 5). Shipping a
Svelte port with the same coverage as the legacy dashboard does not justify
the migration cost.

#### Rationale

The migration's user-facing payoff is "stop silently dropping events". Every
uncovered part type means a worker's behaviour is invisible to the user,
which is the opposite of what a dashboard exists for.

---

### ADR-006 [DASHBOARD]: Copy Legacy CSS Verbatim; Defer Component-Scoped Refactor

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Styling

#### Context

Decomposing legacy CSS into per-component `<style scoped>` blocks during the
migration conflates two changes: structural port and stylistic refactor. If
the migration result looks different from the legacy dashboard, verification
is ambiguous.

#### Decision

Phase 4 copies the entire `<style>` block from `dashboard.html` into
`src/app.css` unchanged. Components keep the same class names as their legacy
counterparts. Scoped component styles are added only for elements the legacy
CSS does not cover.

A follow-up task to decompose `app.css` into per-component blocks is logged
at `features/open/CSS-REFACTOR.md`.

#### Rationale

Visual parity at cutover is a correctness signal. If the migrated dashboard
renders pixel-close to the legacy one, then the port is verified. Starting
from a wholly new styling approach forfeits that signal.

---

### ADR-007 [DASHBOARD]: Use @opencode-ai/sdk For Both Types And Transport

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: API client

#### Context

Three options for talking to OpenCode (via `server.py` proxy):

1. Raw fetch with hand-typed interfaces.
2. `@opencode-ai/sdk` for TypeScript types only, raw fetch for transport.
3. `@opencode-ai/sdk` fully.

#### Decision

Use option 3. Configure the SDK in `lib/api/oc.ts` to target
`http://localhost:7700/api/oc` (not OC directly — `server.py` stays the
proxy).

#### Rationale

The SDK is framework-agnostic (its only runtime dep is `cross-spawn`) and
tracks OC's event schema evolution automatically. Hand-rolling types is
busywork with a perpetual drift tax. Bundle-size impact is negligible for a
local dashboard.

OpenSwarm-native endpoints (task state, orchestrator control) are not covered
by the SDK and use a separate hand-typed client (`lib/api/swarm.ts`).

---

### ADR-008 [DASHBOARD]: Commit Migration Work Directly To main With Staged Commits

- **Date**: 2026-04-24
- **Status**: Accepted
- **Area**: Git workflow

#### Context

`openswarm/AGENTS.md` states "work on main only — there is no PR workflow".
The dashboard migration spans multiple days and many commits, typically a
case for a feature branch.

#### Decision

Keep migration work on `main`. Each phase lands as a small number of
self-contained commits. The repo remains in a working state after every
commit (legacy `dashboard.html` continues to be served until the Phase 5
cutover flips the default).

A detached-HEAD backup worktree at `/home/agent/openswarm-pre-svelte/`
snapshots the state immediately before Phase 1 and remains available as a
rollback reference until it is explicitly removed post-cutover.

#### Rationale

The existing `main`-only rule is chosen for simplicity on a single-maintainer
repo. Introducing a feature branch here is the right call only if the
migration is high-risk enough to warrant isolation. The phased-commit approach
with a filesystem backup provides equivalent rollback safety at lower workflow
overhead.
