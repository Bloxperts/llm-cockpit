# Changelog

All notable changes to **llm-cockpit** are documented here. The project
follows SemVer once it reaches v0.1.0; pre-release alphas use `v0.X.Yaβ`.

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
