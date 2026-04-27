<!-- Status: Live | Updated: 2026-04-27 -->
# LLM Cockpit — Sprint State

**Live document.** Updated at every sprint planning, every spec status transition, and every sprint review.

---

## Current sprint

**Sprint 1** — Architecture sprint.
**Window:** 2026-04-27 (Mon, opened mid-week) → 2026-05-03 (Sun) — runs concurrent with the formal Sprint 0 review at the boundary.
**Goal:** Lock the public-release framing, walk every user / functional / test spec to a coherent state, produce an Accepted architecture document. **No implementation in this sprint.**

### Architecture-sprint deliverables

| Artefact | Target status by 2026-05-03 |
|----------|------------------------------|
| Public release framing | ✅ ADR-003 Accepted |
| Stack revision (pip + CLI, drop scheduler) | ✅ ADR-002 v1.1 Accepted |
| Role ladder + permission model | ADR-004 Accepted |
| `architecture/COMPONENTS.md` v1.0 (public framing) | Accepted |
| `GOALS.md` rewritten for public audience | Accepted |
| User Specs US-01 … US-10 | All in Review |
| Functional Specs US-01 … US-10 | All in Review |
| Test Specs US-01, US-08, US-09 (Sprint 2 candidates) | Review |
| Test Specs US-02 … US-07, US-10 | Draft (filled at sprint start when story enters in-progress) |
| DG-004 blocks on US-02 / US-07 / US-10 | Present |
| LL-INDEX entry on the framing flip | Drafted |

### v0.1 user-story set (10)

| US | Title | Sprint plan | User Spec | Functional Spec | Test Spec |
|----|-------|-------------|-----------|-----------------|-----------|
| US-01 | User logs in | 2 | Review | Review | Review |
| US-02 | Live dashboard (Ollama + optional GPU) | 3 | Review | Review | Draft |
| US-03 | Dashboard history (24 h, 7 d) | 5 | Review | Review | Draft |
| US-04 | Chat interface (chat-tagged models) | 4 | Review | Review | Draft |
| US-05 | Code interface (code-tagged models) | 4 | Review | Review | Draft |
| US-06 | Admin: user management | 6 | Review | Review | Draft |
| US-07 | Ollama integration (`LLMChat` port) | 1 (design) → 2 (build) | Review | Review | Review |
| US-08 | First-run installation + bootstrap | 2 | Review | Review | Review |
| US-09 | First-login forced password change | 2 | Review | Review | Review |
| US-10 | Admin: Ollama configuration + metrics | 7 | Review | Review | Draft |

### Decision Guides binding the architecture sprint

- **DG-004** (port or adapter) — runs on US-02, US-07, US-10 (only these cross the platform boundary in v0.1).
- DG-001 / DG-002 / DG-003 — N/A (no agents; delivery form decided once in ADR-002).

### Out of scope for Sprint 1

- Any code changes beyond the methodology-bootstrap commits already on `feature/SPRINT-0-methodology-bootstrap`.
- Test Specs for US-02 / US-03 / US-04 / US-05 / US-06 / US-10 (filled when their sprint opens; Draft is acceptable through Sprint 1).
- Any v0.2 features (Model Lifecycle pull/delete, conversation export, A/B compare, MCP host).

### Sprint 1 review checklist (run on Sunday 2026-05-03)

- [ ] ADR-003, ADR-004 Accepted in vault and mirrored to `/docs/decisions/`.
- [ ] ADR-002 at v1.1 Accepted.
- [ ] `GOALS.md` and `README.md` reflect public framing — no Bloxperts-internal references in the body.
- [ ] All 10 User / Functional Specs at status Review.
- [ ] Test Specs for the Sprint 2 candidates (US-08, US-09, US-01, US-07) at Review.
- [ ] `architecture/COMPONENTS.md` Accepted.
- [ ] DG-004 blocks present on US-02, US-07, US-10.
- [ ] LL entry drafted for "Architecture framing flipped from internal-Neuroforge to public release".
- [ ] Chris explicitly accepts the four ADRs and the architecture document.

---

## Sprint history

### Sprint 0 — Methodology bootstrap

**Window:** 2026-04-27 (Mon) — single-day sprint, kept short because it runs alongside Sprint 1.
**Outcome:** ✅ closed.

Delivered:

- `process/PROCESS.md` v1.0 Accepted (mirrors AgenticBlox PROCESS v2.0 with four cockpit deltas).
- `design-principles/DP-INDEX.md` v1.0 Accepted (Adopt / Defer / Skip mapping).
- `decisions/ADR-001` (mirror process), `decisions/ADR-002` v1.0 (stack + delivery form), `decisions/ADR-INDEX.md`.
- `lessons-learned/LL-INDEX.md` placeholder.
- `specs/` restructured into three-doc layout (`user/`, `functional/`, `test/`).
- Repo: `develop` branch, `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `scripts/sync-docs-from-vault.sh`, `/docs` mirror first run, two commits on `feature/SPRINT-0-methodology-bootstrap`.

Lessons: the original Spring 0 plan had US-01 going through `Draft → Review → Accepted` in this window. ADR-003 (public framing) intervened mid-sprint and the spec was held at `Review` rather than rushed to `Accepted` under the wrong framing. This is **expected** Spec-First behaviour, not a process miss.

---

## Backlog (post-Sprint-1)

Implementation order proposed:

1. **Sprint 2** — US-08 (installer + bootstrap) → US-09 (first-login change) → US-01 (login). End of Sprint 2 = a freshly-installed cockpit lets `admin` log in, change password, and see an empty dashboard.
2. **Sprint 3** — US-02 (live dashboard) + telemetry sampler.
3. **Sprint 4** — US-04 (chat) and US-05 (code) together — they share machinery.
4. **Sprint 5** — US-03 (dashboard history).
5. **Sprint 6** — US-06 (user management).
6. **Sprint 7** — US-10 (Ollama configuration + metrics).
7. **v0.2 backlog** — Model Lifecycle (pin/unpin/keep_alive), conversation export, A/B compare, pluggable LLMChat adapter, MCP tool-call display.

US-07 (Ollama integration) is treated as cross-cutting: the `LLMChat` port + `OllamaLLMChat` adapter is designed in Sprint 1 and built **incrementally** alongside US-08 in Sprint 2 (the adapter is the bridge the chat / code routers will need in Sprint 4).

---

## Status transitions log

| Date | Item | From | To | By |
|------|------|------|----|----|
| 2026-04-27 | process/PROCESS.md | — | Accepted v1.0 | Chris |
| 2026-04-27 | DP-INDEX.md | — | Accepted v1.0 | Chris |
| 2026-04-27 | ADR-001 | — | Accepted | Chris |
| 2026-04-27 | ADR-002 | — | Accepted v1.0 | Chris |
| 2026-04-27 | Sprint 0 | Open | Closed | Chris |
| 2026-04-27 | ADR-003 (public framing) | — | Accepted | Chris |
| 2026-04-27 | ADR-002 | Accepted v1.0 | Accepted v1.1 | Chris |
| 2026-04-27 | Sprint 1 | — | Open | Chris |
| 2026-04-27 | GOALS.md | Draft | Accepted v1.0 | Chris |
| 2026-04-27 | SPEC-001 (US-01 functional) | Draft | Review | Claude |
| 2026-04-27 | US-01 user spec | Draft | Review | Claude |
| 2026-04-27 | US-01 test spec | Draft | Review | Claude |

(rows added as transitions happen)
