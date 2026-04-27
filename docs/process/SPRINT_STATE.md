<!-- Status: Live | Updated: 2026-04-27 -->
# LLM Cockpit — Sprint State

**Live document.** Updated at every sprint planning, every spec status transition, and every sprint review.

---

## Current sprint

**Sprint 2** — First build sprint.
**Window:** 2026-04-28 (Tue) → 2026-05-04 (Mon) — first build week.
**Goal:** Land the install + login + first-login-change flow on the cockpit. End-of-sprint smoke: `pip install llm-cockpit && cockpit-admin init && cockpit-admin serve`, log in as `admin/ollama`, change password, see an empty dashboard. UC-07's `LLMChat` port + `OllamaLLMChat` adapter is built incrementally as a dependency of UC-08.

### Sprint 2 backlog

| UC | Title | Functional Spec | Plan |
|----|-------|-----------------|------|
| UC-08 | First-run installation + bootstrap | Accepted | Build pip wheel layout (cli.py, main.py, alembic), implement `cockpit-admin {init, serve, doctor, migrate}`, wire bind-interface prompt, seed admin, snapshot model tags. |
| UC-07 | Ollama integration (`LLMChat` port) | Accepted | Build the port + `OllamaLLMChat` adapter + `FakeLLMChat`. UC-08's probe consumes `list_models()` only; the rest of the surface lands as Sprint 4 needs it. |
| UC-09 | First-login forced password change | Accepted | Build `current_user_must_be_settled` dependency, `POST /api/auth/change-password`, validation rules, frontend `/change-password` page. |
| UC-01 | User logs in | Accepted | Build `routers/auth.py`, JWT issuance + cookie, `current_user` dep, `require_role`, login audit. Role-aware redirect. |

End-of-sprint review on 2026-05-04 (Mon).

### Sprint 2 review checklist

- [ ] All four functional specs reach `Done (technical)`.
- [ ] `pytest --cov` ≥ 90 % on `cockpit/cli.py`, `cockpit/services/users.py`, `cockpit/routers/auth.py`, `cockpit/adapters/ollama_chat.py`.
- [ ] Smoke (manual): clean Mac + clean Ubuntu run `pip install` → `init` → `serve` → log in → change password → land on `/dashboard` (empty board).
- [ ] LL entries written for non-trivial discoveries.
- [ ] Sprint Review protocol filed at `process/reviews/SPRINT-02-REVIEW.md`.
- [ ] Chris explicitly accepts the four UCs `Done → User Accepted`.

### Out of scope for Sprint 2

- The placement board UI / drag-drop (UC-02 dashboard front-end).
- The performance harness implementation (server-side wired in Sprint 3 with UC-02).
- Chat / Code pages (Sprint 4).
- Admin user management UI (Sprint 6).
- Admin Ollama config page (Sprint 7).

---

## Sprint history

### Sprint 1 — Architecture sprint (closed 2026-04-27)

**Window:** 2026-04-27 (single-day; ran alongside Sprint 0 boundary).
**Outcome:** ✅ closed.

Delivered:

- ADR-003 Accepted (public release framing).
- ADR-004 Accepted (role ladder `chat < code < admin`).
- ADR-005 Accepted (per-model lifecycle + perf harness back in v0.1; supersedes ADR-003 §6).
- ADR-002 v1.1 Accepted (pip + CLI distribution; scheduler dropped).
- `architecture/COMPONENTS.md` v1.0 Accepted.
- `GOALS.md` v1.0 Accepted (public framing + LAN access + iPhone v2 idea).
- `README.md` v0.2 (vault) updated.
- 10 use cases written and Accepted (UC-01..UC-10) — moved from `specs/user/` to `use-cases/` to match AgenticBlox layout.
- 10 functional specs Accepted.
- Test specs for Sprint-2 candidates (UC-01, UC-07, UC-08, UC-09) at Review; the rest at Draft (filled when their sprint opens).
- Repo: methodology-bootstrap commits + Sprint 1 commits on `feature/SPRINT-0-methodology-bootstrap`.

Lessons:

- Architecture flipped twice mid-sprint (Neuroforge-internal → public framing → public + back-ported model lifecycle). Each flip ended in an ADR; the spec set caught up rather than the other way round. Spec-First held.
- The `specs/user/` → `use-cases/` rename was Chris's correction — AgenticBlox's actual folder layout is the canonical reference; the cockpit aligned mid-sprint.

### Sprint 0 — Methodology bootstrap (closed 2026-04-27)

**Outcome:** ✅ closed.

Delivered: PROCESS.md v1.0, DP-INDEX v1.0, ADR-001, ADR-002 v1.0, lessons-learned/LL-INDEX, restructured specs into three-doc form, repo `develop` branch, `CONTRIBUTING.md`, PR template, `scripts/sync-docs-from-vault.sh`, `/docs` mirror.

---

## Backlog (post-Sprint-2)

1. **Sprint 3** — UC-02 (live dashboard + placement board) + perf harness back-end + telemetry sampler.
2. **Sprint 4** — UC-04 (chat) and UC-05 (code) — share machinery.
3. **Sprint 5** — UC-03 (dashboard history).
4. **Sprint 6** — UC-06 (admin user management).
5. **Sprint 7** — UC-10 (admin Ollama config — heuristic editor, perf history, audit log).
6. **v0.2 backlog** — pluggable LLMChat adapter, conversation export, A/B compare, MCP tool-call display, telemetry adapters beyond `nvidia-smi`, multi-Ollama topology.
7. **v2 ideas** — iPhone client over VPN, voice input.

---

## Status transitions log

| Date | Item | From | To | By |
|------|------|------|----|----|
| 2026-04-27 | Sprint 0 | Open | Closed | Chris |
| 2026-04-27 | ADR-003 (public framing) | — | Accepted | Chris |
| 2026-04-27 | ADR-004 (role ladder) | — | Accepted | Chris |
| 2026-04-27 | ADR-005 (per-model lifecycle in v0.1) | — | Accepted | Chris |
| 2026-04-27 | ADR-002 v1.0 | Accepted | Accepted v1.1 | Chris |
| 2026-04-27 | GOALS.md | Draft | Accepted v1.0 | Chris |
| 2026-04-27 | architecture/COMPONENTS.md | Draft | Accepted v1.0 | Chris |
| 2026-04-27 | UC-01..10 use cases | Review | Accepted | Chris |
| 2026-04-27 | UC-01..10 functional specs | Review | Accepted | Chris |
| 2026-04-27 | UC-01, UC-07, UC-08, UC-09 test specs | Review | Accepted | Chris |
| 2026-04-27 | Sprint 1 | Open | Closed | Chris |
| 2026-04-28 | Sprint 2 | — | Open | Chris |
