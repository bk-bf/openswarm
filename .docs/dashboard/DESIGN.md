<!-- LOC cap: 200 (created: 2026-04-24) -->

# DESIGN [DASHBOARD]

> **Related:** [ARCHITECTURE](ARCHITECTURE.md) · [DECISIONS](DECISIONS.md) · [PHILOSOPHY](PHILOSOPHY.md) · [ROADMAP](features/open/ROADMAP.md)

This doc inherits the principles from the legacy `.docs/DESIGN.md` and adds
Svelte-specific conventions.

## Core UI Principles (inherited from legacy)

- **Viewport efficiency**: avoid vertical scrolling for the primary dashboard
  state. Everything the user needs to triage a run should fit on one screen.
- **No nested borders**: card-in-card nesting is forbidden. One border depth
  per surface.
- **No wasted padding**: 8-12 px is the default gap, not 16-24. Dense is good.
- **Dark theme only**: no light-theme support. Colors defined as CSS variables
  at `:root` in `app.css`.
- **Status conveyed by colour first, text second**: green=ok, amber=in progress,
  red=failed, grey=idle. Text labels accompany but never replace the colour.

## Svelte-specific Conventions

### Loading States

Any component with an unavoidable async wait renders `<LoadingDots />` from
`lib/components/LoadingDots.svelte`. Never leave a blank area or zero-value
data visible while data is in flight. Prop: optional `label` for `aria-label`.

### Component Extraction

Feature logic lives in dedicated `.svelte` components under `src/lib/`. Route
files (`+page.svelte`, `+layout.svelte`) and page-view orchestrators
(`lib/pages/<feature>/<Feature>View.svelte`) are **wiring only** — they:

- import components,
- own top-level state or read context,
- pass props.

They do **not** implement rendering logic, formatting, or feature sub-views
directly. If a block of template exceeds ~30 lines or is reused, extract it.

### State Access Rule

Components never import from `lib/sync/` directly. They always go through a
composable (`lib/composables/use*.svelte.ts`), which returns a focused reactive
slice. This keeps the sync layer swappable and the UI layer testable.

### CSS Handling (during migration)

Phase 4 copies the entire `<style>` block from `dashboard.html` verbatim into
`src/app.css`. No refactor during migration. A future scoped-component CSS
refactor is tracked in `features/open/CSS-REFACTOR.md`.

During migration:
- `src/app.css` is the **only** stylesheet imported.
- Components may add `<style scoped>` blocks **only** for new elements that
  the legacy CSS doesn't cover. Existing classes remain in `app.css`.
- Do not edit legacy class names in `app.css` during the port — matching the
  legacy HTML's class structure is what makes the port verifiable.

### Rune File Extension

TypeScript files that contain Svelte 5 runes (`$state`, `$derived`, `$effect`)
use the `.svelte.ts` extension so the Svelte compiler picks them up. All
composables and the sync context use this extension.

### Context Keys

Each global state context defines a symbol or string key exported from its
own module:

```ts
// lib/sync/sync-context.svelte.ts
export const SYNC_KEY = Symbol('oc-sync');
```

Consumers type the retrieval:

```ts
const ctx = getContext<SyncContext>(SYNC_KEY);
```

### Accessibility

- All interactive elements must have visible focus states.
- Permission prompts use `role="alert"` so screen readers announce them when
  they appear.
- Keyboard-first: Enter submits composer, Esc closes modals, Ctrl+Enter sends
  without newline.

### Dashboard-specific Layout Rules

- The worker card grid uses `display: grid` with `grid-template-columns:
  repeat(auto-fill, minmax(220px, 1fr))`. Do not hard-code column counts.
- The orchestrator panel slides in from the right (`position: fixed`, full
  height, configurable width). It **never** pushes content — it overlays.
- The chat view is always the full content area minus the orchestrator panel
  width. It does not need internal max-width constraints; the monospace text
  handles wrapping.

## Future Component-Based Refactor

Once the 1:1 port is stable, `src/app.css` will be decomposed into per-component
`<style>` blocks. See `features/open/CSS-REFACTOR.md`. Do not pre-empt that work.
