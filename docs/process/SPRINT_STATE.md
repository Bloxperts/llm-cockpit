<!-- Status: Live | Updated: 2026-04-27 -->
# LLM Cockpit — Sprint State

**Live document.** Updated at every sprint planning, every spec status transition, and every sprint review.

---

## Current sprint

**Sprint 0** — Methodology bootstrap.
**Window:** 2026-04-27 (Mon) → 2026-05-03 (Sun).
**Goal:** Establish process, vault scaffolding, repo conventions; carry SPEC-001 (login) from `Draft` to `Accepted` so Sprint 1 can start implementation.

### Backlog committed to Sprint 0

| US | Title | User Spec | Functional Spec | Test Spec | Status |
|----|-------|-----------|-----------------|-----------|--------|
| — | Vault: PROCESS.md (cockpit-local) | n/a | n/a | n/a | Done |
| — | Vault: SPRINT_STATE.md | n/a | n/a | n/a | Done |
| — | Vault: DP-INDEX.md (inheritance map) | n/a | n/a | n/a | In Progress |
| — | Vault: ADR-INDEX, ADR-001, ADR-002 | n/a | n/a | n/a | Pending |
| — | Vault: LL-INDEX placeholder | n/a | n/a | n/a | Pending |
| — | Vault: restructure specs/ to user/functional/test | n/a | n/a | n/a | Pending |
| US-01 | Family member logs in to the cockpit | Draft | Draft | Draft | Spec → Review |
| US-02 | Operator sees live model + GPU + queue state | — | Draft | — | DG-004 block pending |
| US-06 | Admin pins/unpins models, adjusts num_ctx | — | Draft | — | DG-004 block pending |
| US-07 | All chat / code calls go through scheduler | — | Draft | — | DG-004 block pending |
| — | Repo: develop branch + CONTRIBUTING + PR template + sync script | n/a | n/a | n/a | Pending |
| — | Repo: /docs mirror first run | n/a | n/a | n/a | Pending |

### Out of scope for Sprint 0

- Any code changes beyond the scaffold commit `06a67a1` ("Initial scaffold").
- US-02 / US-03 / US-04 / US-05 spec acceptance (they need DG-004 reasoning + dependency on US-01 + US-07 first).
- Production deploy on Neuroforge.

### Sprint 0 review checklist (run on Sunday 2026-05-03)

- [ ] PROCESS.md / SPRINT_STATE.md / DP-INDEX / ADR-INDEX / LL-INDEX all present and Accepted.
- [ ] specs/ is in three-doc layout (user / functional / test).
- [ ] SPEC-001 has user spec Accepted, functional spec Accepted, test spec Accepted.
- [ ] DG-004 blocks present on US-02, US-06, US-07 functional specs.
- [ ] `develop` branch exists; CONTRIBUTING.md and PR template merged to `main`.
- [ ] `/docs/` mirror in repo matches the vault subset.
- [ ] Sprint Review protocol filed at `process/reviews/SPRINT-00-REVIEW.md`.
- [ ] LL entries written for any non-trivial insight.

---

## Backlog (post-Sprint-0)

Implementation order proposed in `docs/STATUS.md`:

1. **Sprint 1** — US-01 (login, frontend `/login` + backend `/api/auth/*`).
2. **Sprint 2** — US-02 (dashboard live) + telemetry sampler.
3. **Sprint 3** — US-07 (scheduler-client module + tests, no UI).
4. **Sprint 4** — US-04 (chat page; depends on US-01 + US-07).
5. **Sprint 5** — US-05 (code page; alias of chat).
6. **Sprint 6** — US-03 (dashboard history).
7. **Sprint 7** — US-06 (admin controls; last because it touches privileged ops).

US-V1 / US-A1 / US-CL / US-EX / US-MCP are v0.2 candidates — not yet planned into a sprint.

---

## Status transitions log

| Date | Item | From | To | By |
|------|------|------|----|----|
| 2026-04-27 | process/PROCESS.md | — | Accepted v1.0 | Chris (this session) |
| 2026-04-27 | Sprint 0 | — | Open | Chris |

---

## Sprint history

(none yet — Sprint 0 is the first.)
