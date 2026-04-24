<!-- LOC cap: 300 (created: 2026-04-24, updated: 2026-04-24) -->

# DESIGN [DASHBOARD]

> **Related:** [ARCHITECTURE](ARCHITECTURE.md) · [DECISIONS](DECISIONS.md) · [PHILOSOPHY](PHILOSOPHY.md) · [ROADMAP](features/open/ROADMAP.md)

Single source of truth for all dashboard UI and Svelte conventions.
The legacy `.docs/DESIGN.md` has been merged here and deleted.

---

## Core UI Principles

### Viewport efficiency

The dashboard is a dense, information-first tool. Every pixel of vertical space
is shared across multiple worker cards visible simultaneously. The following
rules are non-negotiable.

#### No wasted container padding

Strip elements (toolbars, input rows, status bars) that sit flush against a
parent border must carry **zero outer padding**. The border itself provides
the visual separation. Add internal padding only to the interactive element
inside the strip, not to the container.

```css
/* WRONG — outer padding doubles the visual weight */
.chat-input-area {
  padding: 5px 10px 6px;
  border-top: 1px solid var(--border);
}

/* RIGHT — padding lives on the textarea, container is zero-padded */
.chat-input-area {
  border-top: 1px solid var(--border);
}
.chat-textarea {
  padding: 5px 8px;
}
```

#### No nested borders

An element inside a bordered container must not carry its own matching border
on the shared edge — that produces a double-line artifact and visually inflates
the component.

For a textarea + send-button strip:
- The **container** owns the single top border that separates it from content.
- The **textarea** has no border at all (`border: none`).
- The **send button** has only a `border-left` to divide it from the textarea.
  No top, right, or bottom border.

```css
/* Pattern: flush inline input strip */
.chat-input-area {
  display: flex;
  align-items: stretch;
  border-top: 1px solid var(--border);
  background: var(--bg);
}
.chat-textarea {
  flex: 1;
  border: none;
  background: transparent;
  padding: 5px 8px;
}
.chat-send-btn {
  border: none;
  border-left: 1px solid var(--border);
  background: transparent;
  padding: 0 10px;
}
.chat-send-btn:hover:not(:disabled) {
  color: var(--blue);
  background: var(--blue-bg);
}
```

#### `align-items: stretch` not `flex-end`

Using `align-items: flex-end` forces the container to grow tall enough to
bottom-align children, adding implicit vertical space. `align-items: stretch`
lets the container height be determined solely by the tallest child.

#### No fixed heights on flexible inputs

Do not set `height: 26px` on buttons inside a flex strip — this forces the
container to be at least that tall. Let buttons size via padding alone.

#### Multi-row input with toolbar

When an input area needs a secondary control row, stack rows as
`flex-direction: column` on the outer container. Rows are divided by a single
`border-top` on the lower row — never by adding padding to both sides.

```css
.chat-input-orch { flex-direction: column; }
.chat-main-row   { display: flex; align-items: stretch; }
.chat-toolbar    { display: flex; align-items: stretch; border-top: 1px solid var(--border); }
.ct-select       { border: none; border-right: 1px solid var(--border); padding: 3px 6px; }
```

#### Per-item action buttons (hover reveal)

Inline actions (copy / fork / undo) must not occupy space when idle.
Use `opacity: 0` + `transition` and reveal on parent `:hover`.

```css
.sv-msg-actions { opacity: 0; transition: opacity .12s; }
.sv-msg-user:hover .sv-msg-actions { opacity: 1; }
```

### General rules

- **Dark theme only**: no light-theme support. Colors defined as CSS variables
  at `:root` in `app.css`.
- **No nested borders**: card-in-card nesting is forbidden. One border depth per surface.
- **No wasted padding**: 8-12 px default gap, not 16-24.
- **Status conveyed by colour first, text second**: green=ok, amber=in progress,
  red=failed, grey=idle.

---

## Svelte-specific Conventions

### Loading States

Any component with an unavoidable async wait renders `<LoadingDots />` from
`lib/components/LoadingDots.svelte`. Never leave a blank area or zero-value
data visible while data is in flight. Prop: optional `label` for `aria-label`.

### Component Extraction

Feature logic lives in dedicated `.svelte` components under `src/lib/`. Route
files (`+page.svelte`, `+layout.svelte`) and page-view orchestrators
(`lib/pages/<feature>/<Feature>View.svelte`) are **wiring only** — they import
components, own top-level state, and pass props. They do **not** implement
rendering logic. Extract any block >30 lines or reused in more than one place.

### State Access Rule

Components never import from `lib/sync/` directly. They always go through a
composable (`lib/composables/use*.svelte.ts`). This keeps the sync layer
swappable and the UI layer testable.

### CSS Handling (during migration)

Phase 4 copies the entire `<style>` block from `dashboard.html` verbatim into
`src/app.css`. No refactor during migration. A future scoped-component CSS
refactor is tracked in `features/open/CSS-REFACTOR.md`.

During migration:
- `src/app.css` is the **only** stylesheet imported.
- Components may add `<style scoped>` blocks **only** for new elements not
  covered by legacy CSS. Do not rename legacy classes.

### Rune File Extension

TypeScript files containing Svelte 5 runes use the `.svelte.ts` extension.
All composables and the sync context use this extension.

### Context Keys

```ts
// lib/sync/sync-context.svelte.ts
export const SYNC_KEY = Symbol('oc-sync');

// consumer
const ctx = getContext<SyncContext>(SYNC_KEY);
```

### Accessibility

- All interactive elements must have visible focus states.
- Permission prompts use `role="alert"`.
- Keyboard-first: Enter submits composer, Esc closes modals.

### Dashboard-specific Layout Rules

- Worker card grid: `display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))`.
- Orchestrator panel: `position: fixed`, full height, overlays content — never pushes it.
- Chat view fills the content area minus the orchestrator panel width.

---

## Future Component-Based Refactor

Once the 1:1 port is stable, `src/app.css` will be decomposed into per-component
`<style>` blocks. See `features/open/CSS-REFACTOR.md`. Do not pre-empt that work.
