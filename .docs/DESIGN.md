<!-- LOC cap: 120 (created: 2026-04-21) -->

# UI Design Principles — openswarm dashboard

## Viewport efficiency

The dashboard is a dense, information-first tool. Every pixel of vertical space
is shared across multiple worker cards visible simultaneously. The following
rules are non-negotiable.

### No wasted container padding

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
  border-top: 1px solid var(--border);  /* single separator */
}
.chat-textarea {
  padding: 5px 8px;
}
```

### No nested borders

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

### `align-items: stretch` not `flex-end`

Using `align-items: flex-end` forces the container to grow tall enough to
bottom-align children, adding implicit vertical space. `align-items: stretch`
lets the container height be determined solely by the tallest child (the
textarea as it grows), with no extra headroom.

### No fixed heights on flexible inputs

Do not set `height: 26px` on buttons inside a flex strip — this forces the
container to be at least that tall even when the textarea would prefer less.
Let buttons size via padding alone; they will naturally match the textarea
height through `align-items: stretch`.

### Multi-row input with toolbar

When an input area needs a secondary control row (model selector, agent picker,
thinking toggle), stack rows as `flex-direction: column` on the outer container.
Each row is itself a `flex` strip. Rows are divided by a single `border-top` on
the lower row — never by adding padding to both sides of the separator.

```css
/* Pattern: input area with toolbar row below */
.chat-input-orch {
  flex-direction: column;  /* outer container stacks rows */
}
.chat-main-row {
  display: flex;
  align-items: stretch;    /* textarea + send button */
}
.chat-toolbar {
  display: flex;
  align-items: stretch;
  border-top: 1px solid var(--border);  /* single line between rows */
}
.ct-select {
  border: none;
  border-right: 1px solid var(--border);  /* dividers between controls */
  background: transparent;
  padding: 3px 6px;
}
```

Controls inside the toolbar are separated by `border-right` on each item —
no gap, no padding on the toolbar container itself.

### Per-item action buttons (hover reveal)

Inline actions on feed items (copy / fork / undo) must not occupy space when
idle. Use `opacity: 0` + `transition` and reveal on parent `:hover`. Never
show a persistent button row — it consumes vertical space for every item.

```css
.sv-msg-actions {
  opacity: 0;
  transition: opacity .12s;
}
.sv-msg-user:hover .sv-msg-actions {
  opacity: 1;
}
```
