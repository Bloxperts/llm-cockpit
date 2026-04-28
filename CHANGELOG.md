# Changelog

All notable changes to **llm-cockpit** are documented here. The project
follows SemVer once it reaches v0.1.0; pre-release alphas use `v0.X.Yaβ`.

## [v0.1.3] — 2026-04-28 — Dashboard GPU UX + model context display

UI-layer slice on top of v0.1.2. No new use cases or DB tables; falls under
the existing Accepted UC-02 functional spec.

### Added

- **GPU temperature status badge** on each card in the dashboard's GPU
  strip. Replaces the placeholder VRAM/temp gradient bar with a four-level
  status pill keyed off the RTX 3090 (Ampere GPU Boost 4.0) thresholds:
  - **Good** ≤ 70 °C — emerald.
  - **Workload** 71–82 °C — sky.
  - **Throttling** 83–89 °C — amber. GPU Boost starts clock reduction at
    ~83 °C.
  - **Critical** ≥ 90 °C — rose. Approaching TjMax 93 °C, shutdown risk.
  Raw °C is still shown alongside the badge so operators always have the
  number. Hover-title carries the threshold legend. When `temp_c` is null
  (no telemetry), neither badge nor temp line renders.
- **Watts vs. TDP** line on each GPU card:
  `152 W / 350 W` instead of just `152 W`. Current value is colour-coded
  (emerald ≤ 70 %, amber 71–90 %, rose > 90 %) by percentage of the cap.
  TDP comes from `nvidia-smi --query-gpu=power.limit`; falls back to 350 W
  when the column is `[N/A]`.
- **Configured context** line on every model card:
  `ctx 8 192` from `model_config.num_ctx_default`. Displays `ctx —` when
  no row exists or the value is null. Helps admins see each model's VRAM
  budget at a glance without opening the settings drawer.

### Backend

- `GpuSnapshot` (port) gains an optional `max_power_w: int | None`.
- `NvidiaSmiTelemetry` adapter extends its `--query-gpu=` argument with
  `power.limit`; parses the new column with the same `[N/A]` handling as
  the existing nullable columns. Float values are coerced to int.
- `GpuPayload` (Pydantic schema) and `_serialize_gpu` propagate
  `max_power_w` into the `/api/dashboard/snapshot` payload.
- `gpu_snapshot()` test factory in `adapters/fake_telemetry.py` accepts
  `max_power_w=None` by default; existing UC-02 tests build snapshots
  without specifying it and still validate.

### Tests

- `tests/test_uc02_telemetry.py`:
  - `test_sample_parses_canonical_two_gpu_csv` — the canonical CSV row
    now includes the `power.limit` column; assertion includes
    `max_power_w=350`.
  - `test_sample_handles_n_a_columns` — the `[N/A]` row gets a third
    `[N/A]` column; assertion includes `max_power_w is None`.
  - `test_sample_parses_power_limit_when_present` — explicit float
    coercion check (`350.00` → `350`).
- 286 tests collected, all green.

## [v0.1.2] — 2026-04-28 — Chat UX improvements + visual polish

UI-layer slice on top of Sprint 4. No new use cases, ports, or DB tables —
all changes fall under the existing Accepted UC-04 (chat) and UC-05 (code)
functional specs.

### Added

- **Copy button on every fenced code block.** Click → `navigator.clipboard`
  → checkmark confirmation for 1.5 s. Inline code unchanged.
- **Download button on `html`, `markdown`/`md`, `txt`, and `json` code
  blocks.** Client-side `Blob` → `<a download="artifact.{ext}">` → revoke.
  No backend round-trip.
- **Floating scroll-to-bottom button.** Appears via `IntersectionObserver`
  on the message-end anchor when the user has scrolled away from the
  latest reply. Smooth-scrolls back on click.
- **"Think" toggle** on the chat compose toolbar. Persists per-mode in
  `localStorage`. Pipes `think: true` into the stream request body, which
  the backend forwards as `options={"think": true}` to
  `LLMChat.chat_stream`. Models that don't recognise the option (most)
  ignore it silently per Ollama's docs.
- **Session token counter** below the compose box: live progress bar
  (neutral → amber at 80 % → rose at 95 %) plus exact `<used>/<limit>`
  count. Limit comes from `model_config.num_ctx_default` (now exposed on
  `ConversationDetail`); falls back to 8192.
- **Live ⏱ response timer** in the compose bar while the model is
  generating; updates every 100 ms. Frozen elapsed value shows up next to
  the conversation title as `Last response: 3.4 s` once the stream finishes.
- **Visual polish** — Claude-style two-column layout with a dark sidebar,
  light main pane, max-width message column, user-message bubbles with a
  cut bottom-right corner, no-bubble assistant messages with an orange
  avatar badge, syntax-highlighted code blocks
  (`react-syntax-highlighter` / `oneDark` theme) wrapped in a header bar
  carrying the language label + copy/download buttons, redesigned compose
  card with auto-grow textarea and an icon-only send button, and a
  blinking streaming cursor at the end of the in-flight assistant
  message.
- **Dark mode toggle** in `AppHeader` (sun / moon icon). Class-based
  Tailwind 4 strategy (`@custom-variant dark (&:where(.dark, .dark *))`)
  toggles the `.dark` class on `<html>`. Persists via `localStorage`;
  initial render respects the system preference.

### Backend

- `StreamRequest` schema gains `think: bool = False`.
- `stream_reply()` accepts `options: dict | None` and forwards verbatim to
  `LLMChat.chat_stream(options=...)`.
- `ConversationDetail` schema gains `num_ctx_default: int | None`,
  populated by joining the `model_config` row for the conversation's
  current model. `null` when no row exists.

### Tests

- `test_think_true_passes_through_to_chat_stream_options` and
  `test_think_false_omits_options_dict` in `tests/test_uc04_chat.py`.
- `test_conversation_detail_includes_num_ctx_default` (and the null
  case) confirm the new schema field round-trips through the DB join.
- 285 tests collected total (was 281); all green.

## [v0.1.1] — 2026-04-28 — SQLite WAL + embedding model fix

Bug fixes from the first Neuroforge live install.

### Fixed

- **`GpuSampler` `database is locked` errors.** The 5-second background
  insert into `metrics_snapshot` deadlocked against concurrent request
  handlers because SQLite's default journal mode allows only one writer.
  `make_engine` now enables `PRAGMA journal_mode=WAL` and
  `PRAGMA busy_timeout=5000` for every SQLite connection via a
  SQLAlchemy connect-event listener.
- **Performance test crash on embedding-only models.** Models like
  `nomic-embed-text:latest` return HTTP 400 "does not support chat" on
  `chat_stream`. `_drop_model` and the perf-harness `cold_load` stage
  now catch `OllamaResponseError` and emit a clean
  `{"detail": "model_not_supported"}` SSE error event instead of
  bubbling up to the ASGI stack as an `ExceptionGroup`.

## [v0.1.0a2] — 2026-04-28 — Sprint 4: chat + code + Next.js frontend

First **pip-installable** release. Install with:

```
pip install git+https://github.com/Bloxperts/llm-cockpit.git@v0.1.0a2
cockpit-admin init
cockpit-admin serve
```

Open the cockpit at `http://localhost:8080/`, log in as `admin / ollama`,
change the password on first login, land on the live dashboard.

### Added

- **UC-07 — `LLMChat` port + `OllamaLLMChat` adapter.** All five methods
  (`list_models`, `loaded`, `chat_stream`, `pull_model`, `delete_model`)
  with HTTPX async client, the spec's 5 s connect / 900 s read timeouts,
  and parametrised wire-shape contract tests pinning the keys we read
  from Ollama's NDJSON.
- **UC-08 — install + bootstrap + serve.** `cockpit-admin {init, migrate,
  doctor, serve}` and the bundled Next.js frontend.
- **UC-01 — login.** JWT in HttpOnly + SameSite=Strict cookie, sliding
  renewal, 5-fail-per-username lockout, login_audit writes, ADR-004 role
  ladder (chat < code < admin).
- **UC-09 — first-login forced password change.** `current_user_must_be_settled`
  dependency + `POST /api/auth/change-password` validating length ≥ 8,
  match, and not the literal default.
- **UC-02 — live dashboard + placement board.** Telemetry port +
  `NvidiaSmiTelemetry` adapter, two background samplers, snapshot endpoint
  + SSE stream, full admin Ollama router (place / perf-test / pull /
  delete / settings) with single-flight enforcement (ADR-005 §5).
- **UC-04 — chat interface.** Per-user conversations, streaming SSE
  replies, regenerate-last-turn, partial save on disconnect.
- **UC-05 — code interface.** Same shell with `code` role gate +
  default system prompt from settings or bundled fallback.
- **Next.js frontend.** Static export bundled into the wheel — login,
  forced password change, dashboard with placement controls, chat, code
  pages all functional. Renders Markdown + GFM via `react-markdown`.
- **Build tooling.** `Makefile` + `scripts/build-frontend.sh` orchestrate
  the Next.js build → `src/cockpit/frontend_dist/` copy → `python -m
  build` → sdist + wheel.

### Architecture / process

- ADR-001..005 accepted; PROCESS.md v1.0; three-doc spec form (use case
  + functional + test) for every UC; develop / main two-branch flow.
- Three migrations: `0001_initial` (six bootstrap tables), `0002_dashboard`
  (`metrics_snapshot` + `admin_audit`), `0003_chat` (`conversations` +
  `messages`).

### Tests

- 281 automated tests across 11 files. ≥ 90 % coverage on every module
  the SPRINT_STATE.md review checklist tracks.

### Known limitations

- Drag-and-drop placement board is wired as a `<select>` per card in this
  alpha; full `dnd-kit` interaction lands in v0.1.0a3.
- `last_calls` panel on the dashboard returns `[]` — populated once chat
  history surfaces in UC-03 (Sprint 5).
- No telemetry adapter beyond `nvidia-smi`. Apple Silicon / AMD users
  see "no GPU telemetry" — v0.2.
