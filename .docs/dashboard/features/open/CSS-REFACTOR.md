<!-- LOC cap: 100 (created: 2026-04-24) -->

# CSS Refactor — Decompose app.css Into Component-Scoped Styles

> **Parent roadmap:** [ROADMAP](ROADMAP.md) · **Trigger:** Post-cutover, Phase 6

## Status

⏳ Deferred. Do not start until the Svelte dashboard has been in production
use for at least 2 weeks and visual parity with the legacy dashboard has been
verified across all views.

## Context

Phase 4 ports the legacy `<style>` block from `dashboard.html` verbatim into
`src/app.css`. This preserves pixel parity and makes the port verifiable —
any visual difference points to a porting error, not a styling change.

The resulting `app.css` is ~2500 LoC of globally-scoped rules. This works but
makes it harder to reason about which component owns which rule, which rules
can be safely changed, and which classes are dead after refactors.

## Goal

Decompose `app.css` into:

- `src/app.css` — only `:root` CSS variables, resets, base typography, and
  any rules that must remain global.
- Per-component `<style>` blocks — scoped by default, containing rules that
  apply only to the component's template.
- `src/lib/styles/` (if needed) — shared mixins / utility classes that would
  otherwise duplicate across components.

Target: `app.css` < 400 LoC.

## Approach

One component per commit. For each:

1. Identify the class names referenced in the component's template.
2. Find their rules in `app.css`.
3. Move those rules into a scoped `<style>` block in the component.
4. Keep the class names identical (scoped styles don't require renaming —
   Svelte adds a hash suffix automatically).
5. Remove the rules from `app.css`.
6. Run `check.sh` and visually diff against the pre-svelte worktree.

Order of extraction (lowest risk first):

1. Leaf components with no descendants: `LoadingDots`, `StatusChip`,
   `ModelPicker`, `AgentPicker`.
2. Part renderers (`lib/components/parts/*`).
3. Page sub-components: `WorkerCard`, `MessageRow`, `PermissionPrompt`.
4. Feature orchestrators: `WorkerCardList`, `MessageList`, `Composer`,
   `TaskGraph`.
5. Page shells: `DashboardView`, `SessionView`, `SettingsDrawer`, `OrchPanel`.
6. Layout: `AppShellLayout`.

## Global rules to keep in `app.css`

- `:root` variables (colours, fonts, spacing tokens)
- CSS reset (`* { box-sizing: border-box }` etc.)
- Body + html base styles
- Font-face declarations
- Keyframe animations used in multiple places (consider `lib/styles/` instead)

## Verification

After each extraction commit:

1. `pnpm build` in `dashboard/`.
2. Load the dashboard side-by-side with the pre-svelte worktree version.
3. Cycle through every view; any visual drift is a regression and must be
   fixed before the next extraction.

## Exit criteria

- `app.css` contains only variables, resets, base typography, and font-face.
- No component imports a global class from another component.
- Visual parity with the legacy dashboard is preserved end-to-end.
- Dead CSS detected by a future tooling pass (e.g. `stylelint`, or a
  custom grep of `app.css` class names against the component tree) is ≤ 5%.
