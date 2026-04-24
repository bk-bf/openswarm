<!-- LOC cap: 150 (created: 2026-04-24) -->

# PHILOSOPHY [DASHBOARD]

> **Related:** [ARCHITECTURE](ARCHITECTURE.md) · [DESIGN](DESIGN.md) · [DECISIONS](DECISIONS.md)

## Single-user, single-host, local-network

OpenSwarm runs as one user, on one host, reachable over Tailscale. Every
architectural decision that would add value only to multi-user, multi-device,
or cross-region deployments is explicitly rejected here.

Consequences:

- No optimistic offline writes. Actions round-trip to `server.py` which is on
  localhost and always reachable.
- No cross-tab sync. A second tab gets a fresh sync context; state divergence
  is acceptable and self-corrects via SSE.
- No LRU eviction, no prefetch cache, no binary blob handling. Memory pressure
  isn't a realistic concern on a 32 GB host displaying at most dozens of tasks.
- No auth layer in the dashboard. Tailscale + `agent` user isolation are the
  trust boundary. `server.py` stays on `127.0.0.1`.

## Copy, don't lift

Openchamber (`/tmp/openchamber/packages/ui`) is 180k LoC of React written for
a multi-device, multi-session, polished consumer product. Its patterns are
sound — its volume is irrelevant.

Rule: when a sync or rendering problem matches something openchamber solved,
**translate the pattern**, not the code. If the resulting Svelte/TS file is
more than 25% the size of the openchamber original, a feature has been copied
that shouldn't have been.

## `server.py` is the BFF

SvelteKit's `routes/api/*` BFF pattern is **not used**. `server.py` already
proxies OC, reads task state, writes orchestrator config, reads investigator
logs. Duplicating that in TypeScript to get typed fetch wrappers is busywork.

Consequence: the dashboard runs as static files. No Node process. One systemd
unit. One log file. When debugging, there is exactly one place state can come
from.

## Every feature must earn its LoC

LoC caps at the top of every doc are real, not cosmetic. If a doc overflows,
that is a signal that either:

1. The scope is genuinely growing and should be split into sub-docs, or
2. Speculation is leaking into the doc and should be removed.

Same applies to code. If a composable exceeds ~200 LoC, question whether it
should be two composables. If a component exceeds ~300 LoC, question whether
it has too many responsibilities.

## Observability over cleverness

Every SSE event is loggable. Every reducer action is a pure function. Every
state transition writes to a ring buffer accessible via a dev-mode panel.
When a task hangs or a part fails to render, the first response is always
"read the logs", never "reproduce locally and step through".

## Reliability signals, not polish

OpenSwarm's dashboard exists to surface orchestrator state, not to be pretty.
Priorities in order:

1. **Correctness**: the rendered state matches OC + `state.json` exactly.
2. **Reliability**: after a 30s network hiccup, the dashboard self-recovers
   without a page reload.
3. **Completeness**: every OC part type renders; no silent drops.
4. **Performance**: feels responsive during a 20-worker run.
5. **Aesthetics**: dense, readable, consistent.

A feature that improves aesthetics at the cost of reliability or completeness
gets rejected.
