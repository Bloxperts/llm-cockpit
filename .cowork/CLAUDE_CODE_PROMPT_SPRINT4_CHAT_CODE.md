# Claude Code prompt — Sprint 4: UC-04 chat + UC-05 code + Next.js frontend + pip wheel

Paste this verbatim. This is a three-slice sprint. Complete each slice before starting the next.

---

## Repo state

`origin/develop` is at `35b7ff3` — full Sprint 3 stack: install → serve → login →
change-password → live dashboard with placement board, GPU strip, perf harness.

## Read first (before writing a single line)

1. `CLAUDE.md` — rules, Spec-First gate, branch/commit conventions.
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-04-chat-page.md` — **Accepted**.
4. `docs/specs/functional/UC-05-code-page.md` — **Accepted**. Explicitly depends on UC-04.
5. `docs/specs/test/UC-04-chat-page.md` — **Draft stub**. Fill in as the first commit (Slice A Step 0).
6. `docs/specs/test/UC-05-code-page.md` — **Draft stub**. Fill in alongside UC-04's test spec in Step 0.

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/sprint4-chat-code
```

Commit prefix: `[UC-04]`, `[UC-05]`, or `[chore]` as appropriate.
One PR against `develop` when the full sprint is done.

---

## What already exists — do not rebuild

- `src/cockpit/ports/llm_chat.py` — full `LLMChat` port including `chat_stream`.
- `src/cockpit/adapters/ollama_chat.py` — `OllamaLLMChat` with all five methods.
- `src/cockpit/adapters/fake_chat.py` — `FakeLLMChat` with `calls`/`calls_of()` recorder.
- `src/cockpit/routers/auth.py` — `current_user`, `require_role`, `current_user_must_be_settled`.
- `src/cockpit/deps.py`, `src/cockpit/schemas.py`, `src/cockpit/main.py` — wired.
- `src/cockpit/models.py` — all tables through `admin_audit`/`metrics_snapshot`. Missing: `conversations`, `messages`. Add those (see §Slice A Step 1).
- `tests/test_uc07_port.py` — contains one `pytest.skip` for the chat_stream NDJSON wire-shape pinning. **Remove that skip and implement the pinning in this sprint** (see §Slice A Step 3).

---

## SLICE A — Backend: UC-04 chat + UC-05 code

### Step 0 — Fill in both test specs (first commit, no implementation)

**`docs/specs/test/UC-04-chat-page.md`**: fill approach, test cases (T-01..TN), pass criteria.
Cover: conversation CRUD, streaming SSE (token/usage/done/error events), partial-save on abort,
role gate, model picker filter, per-user isolation, regenerate, DB persistence.

**`docs/specs/test/UC-05-code-page.md`**: fill approach, test cases, pass criteria.
Cover: same as UC-04 plus code-mode-specific: default system prompt from settings row,
fallback when missing, role gate (`code` role), picker filter (code/both only),
mode column set to `code`, conversations isolated from `/chat` list.

Flip both headers to `Status: Accepted`. Add `<!-- VAULT-SYNC -->` comments.
Commit: `[UC-04] fill in test specs UC-04 + UC-05 (Draft → Accepted)`.

### Step 1 — Migration 0003

Add to `src/cockpit/models.py`:

```python
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="chat")
    title: Mapped[str] = mapped_column(String, nullable=False, default="New conversation")
    model: Mapped[str] = mapped_column(String, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, ...)
    updated_at: Mapped[datetime] = mapped_column(DateTime, ...)
    __table_args__ = (
        CheckConstraint("mode IN ('chat', 'code')", name="ck_conversations_mode"),
        Index("idx_conversations_user_mode", "user_id", "mode"),
    )

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    usage_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    gen_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, ...)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        Index("idx_messages_conversation_ts", "conversation_id", "ts"),
    )
```

Create `src/cockpit/migrations/versions/0003_chat.py`. Verify with `alembic upgrade head`.

### Step 2 — Shared chat handler service

**`src/cockpit/services/chat.py`** — pure business logic, no FastAPI, injectable:

```python
async def stream_reply(
    *,
    conversation: Conversation,
    user_content: str,
    llm: LLMChat,
    session: Session,
    settings: Settings,
) -> AsyncIterator[dict]:
    """
    Appends the user message, calls llm.chat_stream, yields SSE dicts:
      {"event": "token",  "data": token_text}
      {"event": "usage",  "data": {"prompt_tok": N, "completion_tok": N, "gen_tps": N}}
      {"event": "done",   "data": {"message_id": N}}
      {"event": "error",  "data": {"code": str, "message": str}}
    Persists the assistant message (partial on abort / error).
    """
```

The `mode` argument is implicit in the `conversation` object. Both `/api/chat` and
`/api/code` call this same function.

### Step 3 — Chat_stream wire-shape contract (deferred from UC-07)

Open `tests/test_uc07_port.py`. Find the `pytest.skip` class for chat_stream NDJSON pinning.
**Replace the skip with real parametrised tests** that:
- Pin the exact final-chunk keys: `prompt_eval_count`, `eval_count`, `eval_duration`,
  `prompt_eval_duration`, `total_duration`, `message.content`, `done`.
- Pin the mid-chunk keys: `message.content`, `done`.
- Use `httpx.MockTransport` (same pattern as T-13/T-14). Golden payload captured from
  Ollama 0.x.y — document the version in a comment.

This satisfies the deferred UC-07 AC-5 and the UC-04 dependency on correct usage extraction.

### Step 4 — Routers

**`src/cockpit/routers/chat.py`** — all routes `Depends(require_role("chat"))`:

```
POST   /api/chat                    → 201 { conversation_id, mode: "chat" }
GET    /api/chat                    → list own chat conversations
GET    /api/chat/{id}               → full conversation + messages
POST   /api/chat/{id}/stream        → SSE via stream_reply()
POST   /api/chat/{id}/regenerate    → SSE (re-run last user turn)
PATCH  /api/chat/{id}               → { title?, model?, system_prompt? }
DELETE /api/chat/{id}               → 204
GET    /api/models?tag=chat         → LLMChat.list_models() filtered by model_tags
```

**`src/cockpit/routers/code.py`** — all routes `Depends(require_role("code"))`.
Identical shape with `/api/code` prefix. Differences:
- `mode="code"` on conversation creation.
- `GET /api/models?tag=code` filters to `code` + `both` tagged models.
- Default system prompt: read from `settings` row `code_default_system_prompt`; fall back
  to the hardcoded string in `default_config/code_default_system_prompt.md`.

**Shared `/api/models` endpoint** — add to `src/cockpit/routers/chat.py` (registered once,
used by both pages). Accepts `?tag=chat|code`. Returns list from `LLMChat.list_models()`
joined with `model_tags`; result filtered to matching tag or `both`.

Register both routers in `main.py`: `/api/chat` and `/api/code`.

### Step 5 — Tests

`tests/test_uc04_chat.py` and `tests/test_uc05_code.py`. Cover every AC from the test specs.
Use `TestClient` + in-memory SQLite + `FakeLLMChat`.

Key coverage required:
- Streaming SSE: token events, usage event on final chunk, done event with message_id.
- Partial save when client disconnects mid-stream: message row exists with `error="stream_aborted"`.
- Role gate: `chat` role → 200 on `/api/chat`, 403 on `/api/code`. `code` role → 200 on both.
- Model picker: `?tag=chat` excludes code-only models; `?tag=code` excludes chat-only models.
- Per-user isolation: user A cannot GET/PATCH/DELETE user B's conversation.
- Default system prompt fallback (UC-05).
- Wire-shape: usage fields in `Message` row after a completed stream.

Coverage target ≥ 90% on `routers/chat.py`, `routers/code.py`, `services/chat.py`.

---

## SLICE B — Next.js frontend

This replaces all four placeholder HTML files with a real production frontend.

### Setup

```bash
cd <repo-root>
npx create-next-app@latest frontend \
  --typescript --tailwind --eslint --app --no-src-dir --import-alias "@/*"
cd frontend
npx shadcn@latest init   # accept defaults; base color: neutral; CSS variables: yes
npm install @tanstack/react-query zustand @dnd-kit/core @dnd-kit/sortable \
            react-diff-viewer-continued react-markdown remark-gfm \
            @radix-ui/react-dialog @radix-ui/react-dropdown-menu
```

Add to `frontend/next.config.ts`:
```typescript
const nextConfig: NextConfig = { output: 'export', trailingSlash: true };
```

Add to `frontend/package.json` scripts:
```json
"export": "next build"
```

### Pages to build

All pages call the FastAPI backend at the same origin. No separate API URL config needed
(static export served by FastAPI). Use TanStack Query for all data fetching.
Global `QueryClient` + Zustand `useAuthStore` (holds `{id, username, role, must_change_password}`
from `/api/auth/me`).

**`app/login/page.tsx`** — username + password form. `POST /api/auth/login` → on success
fetch `/api/auth/me` → populate auth store → redirect to `/dashboard`. On 401 show
"Invalid credentials". On 429 show "Too many attempts — retry after {n}s".

**`app/change-password/page.tsx`** — two password fields. `POST /api/auth/change-password`
→ on 200 redirect to `/dashboard`. Show field-level errors from `detail`.
On mount: if `!must_change_password` redirect to `/dashboard`.

**`app/dashboard/page.tsx`** — full React replacement of the HTML placeholder:
- `useDashboardStream()` hook: `EventSource('/api/dashboard/stream')` → Zustand dashboard store.
- Placement board with `@dnd-kit/sortable`. Columns: GPU 0..N, Multi-GPU, On Demand, Available.
  `useDragEnd` calls `POST /api/admin/ollama/models/{model}/place`. Drag handles render only
  for `role === 'admin'`.
- GPU strip header: VRAM bar per GPU using CSS width driven by `vram_used_mb / vram_total_mb`.
- Model cards: name, tag badge, size, loaded indicator, last perf metrics.
- Admin card menu (`@radix-ui/react-dropdown-menu`): Place, Test performance, Pull, Delete.
- "+ Add model" drawer: `POST /api/admin/ollama/models/{model}/pull` → SSE progress bar.
- Perf-test dialog: SSE stage progress + result table.

**`app/chat/page.tsx`** — three-column layout per UC-04 spec:
- Left rail: conversation list from `GET /api/chat`, "New conversation" button.
- Centre: message bubbles (user right / assistant left). `react-markdown` + `remark-gfm`
  for GFM rendering. Code blocks with language label + copy button. Auto-scroll anchor.
  Stop button replaces Send while streaming. Regenerate on last assistant message.
- Right rail (collapsible): model name, `num_ctx` used, total tokens.
- Composer: textarea, Enter sends, Shift+Enter newline, Ctrl/Cmd+K opens model picker overlay.
- Model picker: `GET /api/models?tag=chat` filtered list, most-recently-used first.
- SSE streaming: native `EventSource` on `POST /api/chat/{id}/stream` response URL
  (use fetch + ReadableStream, not EventSource, since POST SSE needs fetch API).

**`app/code/page.tsx`** — identical shell to chat. Differences:
- Fetches from `/api/code/*` and `GET /api/models?tag=code`.
- Default system prompt pre-filled from conversation's `system_prompt` field.
- Inline diff view: detect `--- a/` / `+++ b/` markers in assistant reply →
  render with `react-diff-viewer-continued` instead of code block.
- Copy-as-file button on code blocks (filename from `# filename: foo.py` first line if present).
- Monospace input, "wrap long lines" toggle.

**Route guard** — `app/layout.tsx` root layout: on mount fetch `/api/auth/me`;
if 401 redirect to `/login`; if 409 `must_change_password` redirect to `/change-password`;
populate auth store. Hide `/code` link in sidebar nav if `role === 'chat'`.

### Build + copy

```bash
cd frontend && npm run export
rm -rf ../src/cockpit/frontend_dist
cp -r out/ ../src/cockpit/frontend_dist
```

Create `scripts/build-frontend.sh` that runs the above. Make it executable.
Run it as the final step of Slice B and commit the built `frontend_dist/` to the branch.
The `.gitignore` already tracks `frontend_dist/` — confirm the built assets are committed.

---

## SLICE C — Pip-installable wheel + GitHub release

This is the test gate. Chris won't test until this slice is done.

### Build tooling

**`Makefile`** in repo root:
```makefile
.PHONY: build-frontend build release

build-frontend:
	bash scripts/build-frontend.sh

build: build-frontend
	python -m build

release: build
	@echo "Wheel built. Upload with: twine upload dist/*"
	@echo "Or install from GitHub: pip install git+https://github.com/Bloxperts/llm-cockpit.git@<tag>"
```

**`scripts/build-frontend.sh`** — as above. Guard: print a clear error and exit 1 if `node`
or `npm` is not found.

**`pyproject.toml`** — verify `[tool.setuptools.package-data]` includes `frontend_dist/**/*`
(it already does from UC-08 Slice A — confirm it's still correct after the Next.js build
produces nested directories).

### Release commit

After Slice B has been committed (built `frontend_dist/` present in the branch):

1. Run `python -m build` locally. Verify the wheel contains `cockpit/frontend_dist/` with
   the real Next.js assets (not the old placeholder HTML).
   ```bash
   unzip -l dist/llm_cockpit-*.whl | grep frontend_dist | head -20
   ```
2. Verify clean install in a fresh venv:
   ```bash
   python3.12 -m venv /tmp/test-cockpit-venv
   source /tmp/test-cockpit-venv/bin/activate
   pip install dist/llm_cockpit-*.whl
   cockpit-admin --version
   cockpit-admin doctor --ollama-url http://127.0.0.1:11434
   deactivate && rm -rf /tmp/test-cockpit-venv
   ```
3. Add a `CHANGELOG.md` entry for `v0.1.0a2`:
   - UC-07 LLMChat port + OllamaLLMChat adapter
   - UC-08 install + bootstrap + serve
   - UC-01 login + JWT
   - UC-09 forced password change
   - UC-02 live dashboard + placement board + perf harness
   - UC-04 chat interface
   - UC-05 code interface
   - Next.js frontend

Commit: `[chore] wheel build verified + CHANGELOG v0.1.0a2`.

### GitHub release

```bash
git tag v0.1.0a2
git push origin v0.1.0a2
gh release create v0.1.0a2 dist/llm_cockpit-*.whl dist/llm_cockpit-*.tar.gz \
  --title "v0.1.0a2 — Sprint 4: chat + code + Next.js frontend" \
  --notes "First pip-installable release. Install with:
pip install git+https://github.com/Bloxperts/llm-cockpit.git@v0.1.0a2

Or download the wheel from this release and:
pip install llm_cockpit-0.1.0a2-py3-none-any.whl"
```

Chris installs on Neuroforge with:
```bash
pip install git+https://github.com/Bloxperts/llm-cockpit.git@v0.1.0a2
cockpit-admin init
cockpit-admin serve
```

---

## Spec status edits (vault not mounted — fallback rule)

- `UC-04-chat-page.md`: `Accepted → In Progress` at branch open, `Done (technical)` when tests pass.
- `UC-05-code-page.md`: same.
- Add `<!-- VAULT-SYNC -->` comments.

---

## Coverage targets

```bash
pytest --cov=cockpit.routers.chat \
       --cov=cockpit.routers.code \
       --cov=cockpit.services.chat \
       --cov-report=term-missing
```

≥ 90 % on each. All prior tests (229 + resolved chat_stream skip) must stay green.

---

## Stop and ask Chris if

- Any Ollama streaming API key name differs from what the UC-07 wire-shape test pins — document
  the key and ask before adapting.
- The `react-diff-viewer-continued` API has changed from what's assumed above.
- The clean-venv smoke test in Slice C fails — do not open the PR until it passes.
- A new Python dependency is required beyond what's in `pyproject.toml`.
