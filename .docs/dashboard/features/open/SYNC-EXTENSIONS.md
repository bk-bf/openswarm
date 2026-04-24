<!-- LOC cap: 150 (created: 2026-04-24) -->

# Sync Layer Extensions — Deferred Openchamber Features

> **Parent roadmap:** [ROADMAP](ROADMAP.md) · **Trigger:** When a condition below materialises.

Openchamber's `packages/ui/src/sync/` contains several files that Phase 2
deliberately did not port. Each entry here records the feature, its trigger
condition, and an LoC estimate, so a future agent can evaluate quickly
whether to promote it to in-scope.

Do not add any of these pre-emptively. Each one must be justified by an
observed bug or user complaint.

---

## `optimistic.ts` — Optimistic local writes

- **Openchamber LoC:** 127
- **Svelte port estimate:** ~100
- **Trigger:** Send round-trips to `server.py` feel laggy to the user in a
  measured scenario (not a hunch). Measurement: time from Enter keydown to
  first rendered user-message part > 150 ms consistently.
- **Rationale for deferring:** `server.py` is on localhost. End-to-end
  round-trip should be < 20 ms. If it isn't, fix the root cause first.

---

## `retry.ts` — Failed-send retry with backoff

- **Openchamber LoC:** 60
- **Svelte port estimate:** ~60
- **Trigger:** Users report the send button silently fails when `server.py`
  is momentarily busy. Alternative: `lib/api/*` gets its own retry helper
  covering transient 5xx, without needing sync-layer involvement.
- **Rationale for deferring:** Simpler fetch-level retry suffices for
  localhost.

---

## `session-cache.ts` — Per-session event/part cache

- **Openchamber LoC:** ~300 (subset)
- **Svelte port estimate:** ~150
- **Trigger:** Switching between sessions causes visible re-fetch/flicker, or
  the user report "old session's messages briefly appear when I switch back".
- **Rationale for deferring:** In-memory state keeps messages alive already.
  Only needed if we evict or if we add multi-session navigation beyond one
  active session.

---

## `content-cache.ts` — LRU cache for large text content

- **Openchamber LoC:** ~200
- **Svelte port estimate:** ~100
- **Trigger:** A file part renders inline content > 50 kB and re-rendering
  causes measurable jank; or memory footprint observably grows during a long
  run.
- **Rationale for deferring:** No inline file-content rendering in Phase 3.
  `FilePart.svelte` renders a reference, not the content.

---

## `persist-cache.ts` — Hydrate state from localStorage on page load

- **Openchamber LoC:** ~150
- **Svelte port estimate:** ~80
- **Trigger:** Users report "I have to wait for all events to re-stream after
  every page refresh" and this is annoying in practice.
- **Rationale for deferring:** Page refresh is cheap on localhost. SSE
  recovery + fresh fetch gets state back in < 1 s for typical runs.

---

## `eviction.ts` — Memory pressure cleanup

- **Openchamber LoC:** ~150
- **Svelte port estimate:** N/A
- **Trigger:** Never promote. Single-user local host + 32 GB RAM makes this
  pure complexity.
- **Rationale for rejecting:** Explicitly out of scope (see ADR-004).

---

## `session-prefetch-cache.ts` — Prefetch likely-next sessions

- **Openchamber LoC:** ~150
- **Svelte port estimate:** N/A
- **Trigger:** Never promote. No navigation pattern where prefetching helps.
- **Rationale for rejecting:** OpenSwarm's dashboard has one active session
  view at a time. Prefetch would waste backend cycles.

---

## `binary.ts` — Binary blob handling

- **Openchamber LoC:** ~200
- **Svelte port estimate:** N/A
- **Trigger:** OC workers start producing binary parts (images, PDFs,
  attachments) and OpenSwarm's use case evolves to need to display them.
- **Rationale for rejecting:** No binary content in current OpenSwarm
  workflows. Add only if the workflow changes.

---

## Review cadence

When closing any dashboard bug that looks sync-related, check this file for
a matching trigger. If found, quote it in the fix commit message. This
ensures deferred features get revisited whenever symptoms surface, not only
when someone remembers this file.
