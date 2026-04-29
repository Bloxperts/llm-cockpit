# Claude Code prompt — Sprint 5: Chat/Code UX improvements

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

`develop` contains the full Sprint 4 stack: chat + code backends, Next.js frontend,
wheel build, plus the v0.1.1 hotfix (WAL mode + embedding model perf fix).

PRs for hotfix must be merged before cutting this branch. Confirm:

```bash
git fetch origin
git log origin/develop --oneline -4
```

---

## Read first (before writing a single line)

1. `CLAUDE.md` — rules, stack, Spec-First gate.
2. `docs/process/SPRINT_STATE.md` — confirm Sprint 5 scope.
3. `frontend/src/components/ChatShell.tsx` — the single file that owns 90 % of this work.
4. `src/cockpit/routers/chat.py` — stream endpoint request body (need to add `think` field).
5. `src/cockpit/services/chat.py` — `stream_reply()` options pass-through.

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-chat-ux-improvements
```

Commit prefix: `[ux]`. One PR against `develop` when done.

---

## What already exists — do not rebuild

- `frontend/src/components/ChatShell.tsx` — 2-col layout, sidebar, message list,
  `streamSse()` generator, `react-markdown` + `remark-gfm`, per-message token stats.
- `frontend/src/lib/api.ts` — `streamSse()` async generator, `ApiError`.
- `src/cockpit/routers/chat.py` and `src/cockpit/routers/code.py` — stream endpoints.
- `src/cockpit/services/chat.py` — `stream_reply()` accepts `options: dict`.

---

## Feature 1 — Copy button on code blocks

In the `react-markdown` component renderer inside `ChatShell.tsx`, add a custom
`code` renderer that wraps every fenced code block in a container with:

- A **Copy** icon button (top-right of the block). Use a clipboard SVG icon (inline
  SVG is fine — no new icon library).
- On click: `navigator.clipboard.writeText(code)` then briefly swap the icon to a
  checkmark for 1.5 s to confirm.
- Inline code (no language tag, single-line) does **not** get the button — only
  fenced blocks (`block={true}` / `node.tagName === 'pre'`).

The custom renderer should also apply the correct Tailwind `prose` code styling.

---

## Feature 2 — Download code artifact button

For fenced code blocks tagged **`html`**, **`markdown`** / **`md`**, **`txt`**, or
**`json`**, add a second **Download** icon button next to the Copy button.

On click: create a `Blob` with the appropriate MIME type, generate an object URL,
programmatically click a hidden `<a download="artifact.{ext}">`, then revoke the URL.

Extension + MIME mapping:
| tag | ext | MIME |
|-----|-----|------|
| html | .html | text/html |
| markdown / md | .md | text/markdown |
| txt | .txt | text/plain |
| json | .json | application/json |

This covers the "LLM generates a file and you download it" use case without any
backend changes.

---

## Feature 3 — Scroll-to-bottom button

The message list already has `messagesEndRef` that `scrollIntoView` is called on.
Add a floating **↓** button that appears when the user has scrolled up away from
the bottom:

- Use `IntersectionObserver` on `messagesEndRef` to know whether the end is visible.
- When NOT visible: show a fixed/absolute button anchored to the bottom-right of
  the message pane (e.g. `absolute bottom-4 right-4`).
- On click: `messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })`.
- Tailwind classes: small pill button, neutral background, shadow, z-10.
- The button must NOT appear during initial load / when the list is empty.

---

## Feature 4 — Thinking on/off toggle

Some Ollama models (deepseek-r1, qwen3, etc.) support extended reasoning via the
`think: true` option. Add a toggle to the chat toolbar:

### Frontend (ChatShell.tsx)

- Add `thinkingEnabled: boolean` state, defaulting to `false`.
- Render a **"Thinking"** toggle button in the compose toolbar (next to the model
  picker, or in the bottom bar near Send). Style: a small pill/badge toggle —
  amber when on, neutral when off. Label: "Think".
- When `thinkingEnabled === true`, add `"think": true` to the `options` object
  sent in the stream request body.
- Persist the setting in `localStorage` keyed by `"cockpit_thinking_{mode}"` so
  it survives page reload.

### Backend (stream endpoint + service)

**`src/cockpit/routers/chat.py`** and **`src/cockpit/routers/code.py`**:
Add `think: bool = False` to the stream request body Pydantic model (or
`StreamRequest` schema). Pass it through to `stream_reply()` as part of `options`.

**`src/cockpit/services/chat.py`** — `stream_reply()`:
`options` already exists and is forwarded to `LLMChat.chat_stream(options=...)`.
No change needed if `think` is already part of `options` dict. Verify this is the
case — if options are filtered before forwarding, add `think` to the allowed keys.

The backend must not break when `think=False` or when Ollama ignores the option
(not all models support it — Ollama silently ignores unknown options, per its docs).

---

## Feature 5 — Session token counter in footer

Show a compact token usage bar in the footer of the chat pane:

```
[████████░░░░░░░░░░░░] 3,412 / 8,192 tokens
```

### Token accounting

- **Session tokens**: sum of all `usage_in` + `usage_out` values across the
  messages in `selected.messages` (assistant messages carry these in their DB row
  and are returned in `ConversationDetail`). During streaming, add the live
  streaming token count on top.
- **Token limit**: `selected.num_ctx_default` if set (from `model_config`); fall
  back to a constant `DEFAULT_CTX = 8192` if null/missing. The conversation detail
  endpoint should already return this field — if it doesn't, add it to the
  `ConversationDetail` response schema in `src/cockpit/routers/chat.py`.
- Render as a `<progress>` element or a Tailwind-styled bar. Color: neutral →
  amber at 80 % → rose at 95 %.

### Backend — add `num_ctx_default` to ConversationDetail

Check `src/cockpit/schemas.py` `ConversationDetail`. If `num_ctx_default` is not
already in the response, add it by joining `model_config` when building the
response. If the conversation has no model_config row, return `null`.

---

## Feature 6 — Response time of last call

After each assistant reply completes, show the elapsed time in the message footer:
`3.4 s`.

- Track `sendStart: number | null` (timestamp from `Date.now()`) in ChatShell state.
  Set it at the moment `sendMessage()` / `regenerate()` begins streaming.
- On the "done" SSE event, compute `elapsed = (Date.now() - sendStart) / 1000` and
  store it in `lastResponseMs: number | null` state.
- Render it in the toolbar or below the last assistant message: `Last response: 3.4 s`.
- Reset to null when a new conversation is selected.

---

## Feature 7 — Live "model is working" timer

While the model is actively generating (i.e. `streaming === true`), show a running
counter in the compose area / status bar:

```
⏱ 4.2 s
```

Requirements:
- Uses `useEffect` + `setInterval(100)` — updates every 100 ms while `streaming`.
- Counter starts at 0.0 when `streaming` flips to `true`.
- Stops and **freezes** (does not reset) when `streaming` flips to `false`.
- The frozen value becomes the "response time" for Feature 6 — reuse the same
  `elapsed` state variable; no duplication.
- Render position: left side of the compose bar, replacing or next to the "Send"
  button area. Visible only while streaming (or for 2 s after completion — up to
  you, but don't clutter the UI permanently).

---

## Feature 8 — Visual polish: Claude-style look and feel

Redesign the chat/code UI to match the aesthetic of claude.ai's desktop web client.
This is a pure CSS/markup pass — no logic changes, no new state. All changes live
in `ChatShell.tsx`, `globals.css`, and `AppHeader.tsx`.

### Reference model (what to target)

claude.ai local install: dark sidebar + clean light main area (default light mode),
Inter / system-ui font stack, generous line-height, message bubbles that feel like
a quality notes app, and syntax-highlighted code blocks.

### Layout

- **Sidebar** (`w-64 flex-shrink-0`):
  - Background: `bg-neutral-900` (dark). Text: `text-neutral-100`.
  - Conversation list items: subtle hover `bg-neutral-800`, active `bg-neutral-700`.
  - "New conversation" button at the top: full-width, rounded, `bg-neutral-800`
    hover `bg-neutral-700`.
  - No border-right — just the contrast between dark sidebar and light main pane.

- **Main pane** (`flex-1 flex flex-col`):
  - Background: `bg-white dark:bg-neutral-950`.
  - Max-width of the message column: `max-w-3xl mx-auto w-full` so messages don't
    stretch on wide screens.

### Messages

- **User message bubble**:
  - Right-aligned, `max-w-[75%]`, `ml-auto`.
  - Background: `bg-neutral-100 dark:bg-neutral-800`.
  - Border-radius: `rounded-2xl rounded-br-sm` (cut the bottom-right corner for a
    classic chat look).
  - Padding: `px-4 py-3`.
  - Font: `text-sm text-neutral-900 dark:text-neutral-100`.

- **Assistant message**:
  - Left-aligned, no bubble background — flows directly on the page background,
    like claude.ai.
  - A small circular avatar badge on the left: 28 px circle, `bg-orange-500` (or
    brand amber), containing the letter "C" in white. Use absolute/flex positioning.
  - The text column has `ml-10` to clear the avatar.
  - Font: `text-sm text-neutral-800 dark:text-neutral-200`, `leading-relaxed`.
  - `prose prose-neutral dark:prose-invert max-w-none` for markdown content.

- **Message spacing**: `gap-6` between messages, `py-8` padding at top and bottom
  of the list so messages don't butt against the toolbar edges.

- **Streaming cursor**: while `streaming === true` and content is accumulating,
  append a blinking `|` cursor (`animate-pulse` or a CSS keyframe) at the end of
  the last assistant message. Remove once streaming stops.

### Code blocks (visual upgrade, extends Feature 1+2)

- Use **`react-syntax-highlighter`** (already a common dep — check package.json;
  if absent, `npm install react-syntax-highlighter @types/react-syntax-highlighter`).
- Theme: `oneDark` (dark background, matches claude.ai code blocks).
- Wrapper: `rounded-xl overflow-hidden` with a header bar:
  ```
  ┌──────────────────────────── python ──── [Copy] [Download?] ┐
  │  (syntax-highlighted code)                                  │
  └─────────────────────────────────────────────────────────────┘
  ```
  - Header bar: `bg-neutral-800 px-4 py-1.5 flex items-center justify-between`.
  - Language label: `text-xs text-neutral-400 font-mono`.
  - Copy + Download buttons from Features 1 and 2 move here.

- Inline code: `bg-neutral-100 dark:bg-neutral-800 px-1.5 py-0.5 rounded text-sm
  font-mono text-rose-600 dark:text-rose-400`.

### Compose area

- Full-width card at the bottom, `bg-white dark:bg-neutral-900` with a top border
  `border-t border-neutral-200 dark:border-neutral-800`.
- Inner box: `rounded-2xl border border-neutral-300 dark:border-neutral-700
  bg-white dark:bg-neutral-900 shadow-sm` — matches the claude.ai input area.
- `<textarea>` inside: no border/outline of its own, transparent background,
  `resize-none`, auto-grows to `max-h-48` then scrolls.
- Send button: small, icon-only (↑ arrow SVG), `rounded-full`, `bg-neutral-900
  dark:bg-white text-white dark:text-neutral-900`, placed inside the box
  bottom-right. Disabled (greyed out) when `draft` is empty or `streaming`.
- Token counter (Feature 5) sits below the inner box, left-aligned, `text-xs
  text-neutral-400`.
- Live timer (Feature 7) and Thinking toggle (Feature 4) sit above the inner box,
  right and left respectively, `text-xs`.

### AppHeader

- Height: `h-14`. Background: `bg-white dark:bg-neutral-900` with a bottom border
  `border-b border-neutral-200 dark:border-neutral-800`.
- Left: `LLM Cockpit` wordmark in `font-semibold text-neutral-900 dark:text-white`.
- Center: nav links (Dashboard / Chat / Code) — pill-style active indicator.
- Right: username badge + logout button.

### Typography

In `globals.css`, add:

```css
:root {
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* Prose overrides for chat */
.prose pre { margin: 0; }
.prose code::before, .prose code::after { content: ""; }
```

### Dark mode

All colours above include `dark:` variants. The Tailwind `dark` class strategy is
`class` (verify `tailwind.config.ts` — add `darkMode: 'class'` if missing).
AppHeader should render a **dark mode toggle** button (moon/sun SVG icon) that
toggles the `dark` class on `<html>` and persists via `localStorage`.

---

## Spec note (Spec-First gate)

These features are UI-layer refinements (no new use cases, no new DB tables,
no new ports). They all fall under the existing UC-04 (chat) and UC-05 (code)
functional specs as "frontend fidelity improvements". The Spec-First gate is
satisfied by the Accepted specs.

Update `docs/process/SPRINT_STATE.md` header to note these as in-progress.

---

## Build + release

After all features pass a quick manual smoke on local dev server (`npm run dev`):

```bash
make build   # runs scripts/build-frontend.sh + python -m build
```

Then open a PR:

```bash
gh pr create \
  --base develop \
  --head feature/UC-chat-ux-improvements \
  --title "[ux] Chat/Code UI: copy, download, thinking, tokens, timers, visual polish" \
  --body "Seven UX improvements to the chat/code interface:
- Copy button on all fenced code blocks (with checkmark confirmation)
- Download button for html/md/json/txt blocks (client-side Blob download)
- Scroll-to-bottom floating button when user has scrolled up
- Thinking toggle (think: true option pass-through to Ollama)
- Session token counter with progress bar in footer
- Response time + live streaming timer in compose bar
- Full visual polish: Claude-style layout, dark sidebar, avatar badges, syntax highlighting (react-syntax-highlighter / oneDark), compose box redesign, dark mode toggle

Backend change: adds \`think: bool\` field to stream request body (ignored silently by models that don't support it)."
```

Merge after tests pass:

```bash
gh pr merge --squash \
  --subject "[ux] Chat/Code UI: copy, download, thinking, tokens, timers, visual polish" \
  --delete-branch=false
```

Tag v0.1.2:

```bash
git checkout develop && git pull
git tag v0.1.2
git push origin v0.1.2
gh release create v0.1.2 dist/llm_cockpit-0.1.2-py3-none-any.whl \
  --title "v0.1.2 — Chat UX improvements + visual polish" \
  --notes "**Chat/Code UX**
- One-click copy on code blocks (checkmark confirmation)
- Download code artifacts (HTML, MD, JSON, TXT) direct from chat — no backend needed
- Scroll-to-bottom floating button when scrolled up
- Thinking (extended reasoning) toggle — works with deepseek-r1, qwen3, etc.
- Session token counter with colour-coded progress bar (neutral → amber at 80% → rose at 95%)
- Live ⏱ response timer while model is generating; freezes to elapsed time on completion

**Visual polish (Claude-style)**
- Dark sidebar, clean light main pane, max-width message column
- User messages: right-aligned bubble with cut corner
- Assistant messages: no bubble, left-aligned with orange avatar badge, prose typography
- Syntax-highlighted code blocks (react-syntax-highlighter / oneDark theme) with language label
- Redesigned compose area: rounded inner box, icon-only send button, auto-grow textarea
- Dark mode toggle in AppHeader; persists via localStorage"
```

---

## Stop and ask Chris if

- The `ConversationDetail` schema already exposes `num_ctx_default` — if yes,
  skip that backend change; if not, add it.
- The stream request body schema name differs from what's here — check the actual
  Pydantic model in `routers/chat.py` before adding `think`.
- Any Tailwind utility you reach for isn't available in the static CDN build —
  switch to inline styles as a fallback rather than blocking.
- `localStorage` use conflicts with the SSR static export — guard with
  `typeof window !== "undefined"` checks.
