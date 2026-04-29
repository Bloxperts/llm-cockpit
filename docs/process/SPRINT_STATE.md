<!-- Status: Live | Updated: 2026-04-29 -->
# LLM Cockpit — Sprint State

**Live document.** Updated at every sprint planning, every spec status transition, and every sprint review.

---

## Current sprint

**Sprint 10 (planning, not yet open) — Perf-test progress UI + GPU1 placement diagnose.**

**Window:** TBD — kicks off when Chris pastes the kickoff prompt into Claude Code.
**Target release:** `v0.4.0`.

**Goal**

- Fix the "shows only 'connecting'" UX on `POST /api/admin/ollama/models/{model}/perf-test` per UC-02 v1.1 (Accepted 2026-04-29). SSE event types (`stage` / `progress` / `heartbeat` / `result` / `cancelled` / `error`), cancel route, frontend drawer with live tokens / cancel button / stalled detection.
- Diagnose the GPU1 placement bug: drag-to-GPU1 doesn't reliably land there. Deliverable: a `lessons-learned/LL-NNN-gpu1-placement.md` with reproduction + root-cause hypothesis + recommendation. No fix commit without Chris's explicit go.

### Sprint 10 backlog

| Item | Spec | Plan | Status |
|---|---|---|---|
| UC-02 v1.1 perf-test progress UI | Functional Spec Accepted (v1.1, 2026-04-29) | SSE-event refactor on the existing perf-test endpoint + cancel route + frontend drawer with live progress, cancel button, stalled banner. No new migration. | Pending |
| BUG-GPU1 placement diagnose | n/a (LL doc deliverable) | Reproduce; inspect `actual.mismatch` + `actual.main_gpu_actual`; decide knob (Ollama best-effort / env var / process-per-GPU / code fix). | Pending |

**Out of scope:** UC-08 Slice E (first-run installer Part B reconcile — separate ticket). v2 backlog (external access, mobile/PWA).

---

## Released versions

| Tag | Date | Sprint | What shipped |
|---|---|---|---|
| `v0.3.1` | 2026-04-28 | 9 | UC-10 admin Ollama config page (`/admin/ollama` four panels: tags, defaults, metrics, audit log + CSV export). 431 tests green. PR #16 → 210c2f4. |
| `v0.3.0` | 2026-04-28 | 8 | UC-03 dashboard history (`/dashboard` Live/History tabs, 24h/7d, 4 chart cards). Migration `0005_history.py`. Aggregators every 60 s / 3600 s. Recharts dep. PR #15 → 5ba134f. |
| `v0.2.1` | 2026-04-28 | 7 | Auth UX + session control: optimistic chat bubble, AppHeader user menu (change-pw + session-TTL dropdown), `session_ttl_days`, `token_version` + revoke-sessions, `is_active` + deactivate/reactivate (last-active-admin guard). Migration `0004_auth_ux.py`. 365 tests green. PR #14 → 424a7c7. |
| `v0.2.0` | 2026-04-28 | 6 | UC-06 user management (5 endpoints, last_login_at + token totals) + code workspace (4 file endpoints, FilesPanel, Save-to-workspace). 339 tests green; coverage 94–96 %. Commit 0da9226. |
| `v0.1.3` | 2026-04-28 | 5b | Dashboard GPU UX: GPU temp status badge (RTX 3090 thresholds), watts/TDP from nvidia-smi `power.limit` (350 W fallback, % colour-coded), model ctx line on placement cards. 286 tests green. Commit 5520afd. |
| `v0.1.2` | 2026-04-27 | 5 | Chat/Code UX polish (8 features): copy button, artifact download, scroll-to-bottom, think toggle, session token counter, response time, live timer, visual polish (dark sidebar, syntax highlight, compose card redesign, dark-mode toggle). 285 tests green. Commit dc24ac7. |
| `v0.1.1` | 2026-04-27 | hotfix | SQLite WAL mode (`db.py`); embedding model crash in perf test (`admin_ollama.py _drop_model` + cold_load catches `OllamaResponseError`). Commit 6311e99 (hotfix in fcb772d). |
| `v0.1.0a2` | 2026-04-27 | 4 | UC-04 (chat router) + UC-05 (code router) + Next.js frontend stood up. Migration `0003_chat.py`. Commit 8a5b5a3. |

**develop HEAD:** `97602d7` (UC-02 v1.1 docs sync, 2026-04-29).
**Migrations on disk:** `0001_initial`, `0002_dashboard`, `0003_chat`, `0004_auth_ux`, `0005_history`. Next free: `0006_*`.

---

## Sprint history

### Sprint 9 — UC-10 Admin Ollama configuration (closed 2026-04-28, v0.3.1)

**Outcome:** ✅ closed.

Delivered:
- UC-10 Functional Spec → Done (technical).
- Tag CRUD endpoints (`PATCH/DELETE /api/admin/ollama/models/{model}/tag`).
- Settings GET/PUT (`code_default_system_prompt`, `tag_heuristics_yaml`) with malformed-YAML safety: 400 before DB write, good rows preserved.
- Per-model metrics rollup (last 7 d, assistant rows only) + drill-down with p95.
- Unified audit log (`/api/admin/audit`) merging `login_audit` + `admin_audit` + CSV export.
- `/admin/ollama` page with 4 collapsible panels.
- 37 new tests; 431 total green; coverage 91-94 % on touched modules.

Notes / spec deviations:
- p95 latency omitted from the rollup `GET /metrics` by design (full row pull avoided). Drill-down endpoint computes p95 in Python.
- `services/metrics.ModelStateSampler` triggers `reapply_heuristics` for newly-discovered models — auto-tagging without manual intervention.

### Sprint 8 — UC-03 Dashboard history (closed 2026-04-28, v0.3.0)

**Outcome:** ✅ closed.

Delivered:
- UC-03 Functional Spec → Done (technical).
- `/dashboard` Live/History tab bar; 24 h (1-min buckets) and 7 d (1-h buckets).
- 4 chart cards (Recharts ^3.8.1): GPU Temperature, VRAM Used, Request rate, Latency p50/p95.
- `GET /api/dashboard/history?range=24h|7d&metric=gpu_temp|vram|calls|latency|tokens` with uniform `{ series: [{ label, data: [{ ts, value }] }] }` shape.
- `MinuteAggregator` (every 60 s; prunes raw `metrics_snapshot` > 7 d) and `HourAggregator` (every 3600 s; prunes minute table > 30 d).
- Migration `0005_history.py`.

Notes / spec deviations:
- Spec said "hourly batch" for the minute aggregator. Implementation runs every 60 s so the 24 h chart updates within a minute of the most recent sample. Documented in CHANGELOG.

### Sprint 7 — Auth UX + session control (closed 2026-04-28, v0.2.1)

**Outcome:** ✅ closed.

Delivered:
- Optimistic chat bubble (no UC; UX polish).
- AppHeader user menu (change-password + session-TTL dropdown).
- `session_ttl_days` + `PATCH /session-ttl`.
- `token_version` + revoke-sessions.
- `is_active` + deactivate/reactivate with last-active-admin guard.
- Migration `0004_auth_ux.py`. 365 tests green.

### Sprint 6 — UC-06 user management + code workspace (closed 2026-04-28, v0.2.0)

**Outcome:** ✅ closed.

Delivered:
- UC-06 Functional Spec → Done (technical).
- 5 user-management endpoints (last_login_at + token totals; role/password/delete actions).
- 4 file endpoints; FilesPanel in Code sidebar; Save-to-workspace on CodeBlock.
- 339 tests green; coverage 94-96 %.

### Sprint 5b — Dashboard GPU UX polish (closed 2026-04-28, v0.1.3)

**Outcome:** ✅ closed (mini-slice).

Delivered:
- GPU temp status badge with RTX 3090 thresholds (gradient removed).
- Watts/TDP from `nvidia-smi power.limit` (350 W fallback, % colour-coded).
- Model context-size line on placement cards.
- 286 tests green.

### Sprint 5 — Chat/Code UX polish (closed 2026-04-27, v0.1.2)

**Outcome:** ✅ closed (no new functional spec; UI polish against UC-04 + UC-05).

Delivered (8 features):
- Copy button on code blocks, artifact download, scroll-to-bottom, think/reasoning toggle, session token counter, response time, live timer during generation, full visual polish (dark sidebar, syntax highlight, compose card redesign, dark-mode toggle). 285 tests green.

### v0.1.1 hotfix (2026-04-27)

- SQLite WAL mode in `db.py`.
- Embedding model crash in perf test: `admin_ollama.py _drop_model` + `cold_load` now catch `OllamaResponseError`.

### Sprint 4 — UC-04 + UC-05 chat + code (closed 2026-04-27, v0.1.0a2)

**Outcome:** ✅ closed.

Delivered:
- UC-04 (chat router) + UC-05 (code router) Functional Specs → Done (technical).
- Next.js frontend stood up.
- Migration `0003_chat.py`.

### Sprint 3 — UC-02 live dashboard + placement board (closed 2026-04-27)

**Outcome:** ✅ closed.

Delivered:
- UC-02 v1.0 Functional Spec → Done (technical).
- Live dashboard, placement board, perf harness backend (per ADR-005), GPU + model-state samplers.
- Migration `0002_dashboard.py`. Commit 35b7ff3.

Spec deviations carried forward (rolled into UC-02 v1.1 amendment 2026-04-29):
- Sprint 3 frontend was plain HTML + inline JS (Next.js + dnd-kit shipped in Sprint 4). Drag-drop replaced with per-card `<select>`.
- `last_calls` returned `[]` until UC-04 (Sprint 4) wrote the messages table.
- `_probe_max_context` walks contexts largest-first; spec didn't pin the search strategy.

### Sprint 2 — Install + login + first-login-change (closed 2026-04-27, v0.1.0)

**Outcome:** ✅ closed.

Delivered:
- UC-08 part A (installer skeleton: cli, alembic 0001, init/migrate/doctor) → Done (technical) PR #1 commit 98e2d1f.
- UC-07 (LLMChat port + OllamaLLMChat adapter + FakeLLMChat) → Done (technical) commit 9c6cd2f.
- UC-08 part B (`cockpit-admin serve`, FastAPI main, frontend bundle) → Done (technical) commit 67d4bfe.
- UC-09 (first-login forced password change) + UC-01 (login + JWT + role gate) → Done (technical) commit 330f1a6.
- Migration `0001_initial.py` (six tables).

### Sprint 1 — Architecture sprint (closed 2026-04-27)

**Outcome:** ✅ closed.

Delivered:
- ADR-003 Accepted (public release framing).
- ADR-004 Accepted (role ladder `chat < code < admin`).
- ADR-005 Accepted (per-model lifecycle + perf harness in v0.1; supersedes ADR-003 §6).
- ADR-002 v1.1 Accepted (pip + CLI distribution; scheduler dropped).
- `architecture/COMPONENTS.md` v1.0 Accepted.
- `GOALS.md` v1.0 Accepted.
- 10 use cases + 10 functional specs Accepted.
- Test specs for Sprint-2 candidates (UC-01, UC-07, UC-08, UC-09) Accepted; rest at Draft (filled at sprint open).

Lessons:
- Architecture flipped twice mid-sprint (Neuroforge-internal → public framing → public + back-ported model lifecycle). Each flip ended in an ADR; the spec set caught up.
- The `specs/user/` → `use-cases/` rename mid-Sprint-1 — AgenticBlox layout is the canonical reference.

### Sprint 0 — Methodology bootstrap (closed 2026-04-27)

**Outcome:** ✅ closed.

Delivered: PROCESS.md v1.0, DP-INDEX v1.0, ADR-001, ADR-002 v1.0, lessons-learned/LL-INDEX, three-doc spec layout, repo `develop` branch, PR template, `scripts/sync-docs-from-vault.sh`, `/docs` mirror.

---

## Backlog (post-Sprint-9)

1. **Sprint 10** — UC-02 v1.1 perf-test progress UI + GPU1 placement diagnose. → target `v0.4.0`.
2. **PyPI publish sprint** — once feature-complete, set up PyPI account + `[project]` metadata audit + GitHub Actions OIDC trusted-publisher workflow. Reference: memory `project_pypi_goal.md`.
3. **UC-08 Slice E reconcile** — first-run installer Part B status — verify against shipped code; close or reopen as needed.
4. **Vault drift cleanup** — Sprint 2-7 implementation drift notes were stranded as `<!-- VAULT-SYNC: -->` comments in `/docs` and have been consolidated. Future improvement: add a sprint-end mirror-back step to PROCESS.md §8.
5. **v2 backlog** — external access (VPN / Reverse Proxy / OIDC), Mobile/PWA. Reference: memory `project_v2_backlog.md`.

---

## Status transitions log

| Date | Item | From → To | By |
|------|------|-----------|----|
| 2026-04-27 | Sprint 0 | Open → Closed | Chris |
| 2026-04-27 | ADR-003, ADR-004, ADR-005 | — → Accepted | Chris |
| 2026-04-27 | ADR-002 | Accepted v1.0 → Accepted v1.1 | Chris |
| 2026-04-27 | GOALS.md, COMPONENTS.md | Draft → Accepted v1.0 | Chris |
| 2026-04-27 | UC-01..UC-10 use cases | Review → Accepted | Chris |
| 2026-04-27 | UC-01..UC-10 functional specs | Review → Accepted | Chris |
| 2026-04-27 | UC-01, UC-07, UC-08, UC-09 test specs | Review → Accepted | Chris |
| 2026-04-27 | Sprint 1 | Open → Closed | Chris |
| 2026-04-27 | Sprint 2 | Open → Closed (v0.1.0) | Chris |
| 2026-04-27 | UC-08 part A, UC-07, UC-09, UC-01, UC-08 part B | Accepted → Done (technical) | Chris (sprint review) |
| 2026-04-27 | Sprint 3 | Open → Closed (incl. in v0.1.0) | Chris |
| 2026-04-27 | UC-02 v1.0 | Accepted → Done (technical) | Chris |
| 2026-04-27 | Sprint 4 | Open → Closed (v0.1.0a2) | Chris |
| 2026-04-27 | UC-04, UC-05 | Accepted → Done (technical) | Chris |
| 2026-04-27 | UC-04, UC-05, UC-07 test specs | Draft → Accepted (filled in /docs during sprint, mirrored back to vault 2026-04-29) | Chris |
| 2026-04-27 | Sprint 5 | Open → Closed (v0.1.2; no new UC) | Chris |
| 2026-04-28 | Sprint 5b | Open → Closed (v0.1.3; no new UC) | Chris |
| 2026-04-28 | Sprint 6 | Open → Closed (v0.2.0) | Chris |
| 2026-04-28 | UC-06 | Accepted → Done (technical) | Chris |
| 2026-04-28 | Sprint 7 | Open → Closed (v0.2.1; no new UC) | Chris |
| 2026-04-28 | Sprint 8 | Open → Closed (v0.3.0) | Chris |
| 2026-04-28 | UC-03 | Accepted → Done (technical) | Chris |
| 2026-04-28 | UC-03 test spec | Draft → Accepted | Chris |
| 2026-04-28 | Sprint 9 | Open → Closed (v0.3.1) | Chris |
| 2026-04-28 | UC-10 | Accepted → Done (technical) | Chris |
| 2026-04-28 | UC-10 test spec | Draft → Accepted | Chris |
| 2026-04-29 | UC-02 functional spec | Accepted v1.0 → Accepted v1.1 (perf-test progress UI amendment) | Chris |
| 2026-04-29 | Sprint 10 | — → Planning | Chris |
