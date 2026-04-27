<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# LLM Cockpit — Development Process

**Status:** Accepted
**Version:** 1.0
**Date:** 2026-04-27

The cockpit follows the same Spec-First + 1-week-sprint discipline as AgenticBlox. Per **DP-024 (Vault as Source of Truth)** the Vault copy at `020 Projects/LLM-Cockpit/process/PROCESS.md` is authoritative; the mirror in the GitHub repo at `docs/PROCESS.md` tracks it.

---

## 1. Inheritance from AgenticBlox

The canonical process is `020 Projects/AgenticBlox/process/PROCESS.md` v2.0 (Accepted 2026-04-22). Everything in that document applies here **unchanged**, except for the deltas listed in §3 below.

If something is unclear or ambiguous, defer to AgenticBlox PROCESS v2.0.

---

## 2. Core principle (recap)

**Nothing is implemented without an Accepted Functional Spec.**
**Nothing is Done without User Acceptance.**

Status flow:

```
Draft → Review → Accepted → In Progress → Done (technical) → User Accepted
```

`Review→Accepted` and `Done→User Accepted` always require **Chris's explicit OK**.

---

## 3. Cockpit deltas vs. AgenticBlox PROCESS v2.0

The cockpit is smaller and shorter-lived than AgenticBlox, and it is a UI in front of the Neuroforge stack — not an agent platform. Three deltas apply:

### 3.1 — Three-doc spec form is mandatory from day one

Per AgenticBlox PROCESS §3, every User Story has three artefacts:

- `specs/user/US-NN-short-title.md` — User Specification (what the user wants).
- `specs/functional/US-NN-short-title.md` — Functional Specification (how the system delivers it).
- `specs/test/US-NN-short-title.md` — Test Specification (how it is verified).

The historical seven `SPEC-NNN-*.md` drafts at `specs/SPEC-001-*.md` … `specs/SPEC-007-*.md` are migrated into this three-doc layout in Sprint 0; see ADR-001.

### 3.2 — No agent identity step

PROCESS §9 (Agent Identity at Implementation Time) does **not apply**. The cockpit ships no agents. References to `Blox-<function>` placeholders are unnecessary in cockpit specs.

### 3.3 — `develop` / `main` two-branch flow active from day one

PROCESS §7 notes that AgenticBlox is single-branch (`main` only) until the runtime opens in Block 4. The cockpit runtime exists today, so the two-branch flow is active from Sprint 0:

```
feature/US-NN-short-title
        ↓ (PR after Functional Tests pass)
    develop   ←→  Test environment on Neuroforge :8081 (when it exists)
        ↓ (PR after User Acceptance)
      main    ←→  Prod environment on Neuroforge :8080
```

Commit prefix: `[US-NN] short description of what changed`.
Tags on `main`: SemVer `vX.Y.Z` (X = architecture change, Y = feature, Z = patch).

### 3.4 — Decision Guides that apply

Of the four DGs in AgenticBlox `decision-guides/`:

| DG | Applies to cockpit? | Why |
|----|---------------------|-----|
| DG-001 (should this be an agent?) | No | Cockpit ships no agents. |
| DG-002 (should this agent be split?) | No | See DG-001. |
| DG-003 (what delivery form?) | Once | Already decided: web service. Logged in ADR-002, not re-run per spec. |
| **DG-004 (port or adapter?)** | **Yes — binding** | The cockpit crosses the platform boundary in three places: scheduler client (port 8001), Ollama client (port 11434), `nvidia-smi` telemetry. Every Functional Spec that touches these must include a filled-in DG-004 block. |

A Functional Spec that should carry a DG-004 block but doesn't is **not review-ready**. Same weight as a missing acceptance criterion.

### 3.5 — Lessons-Learned link with AgenticBlox

Cockpit-local lessons land in `020 Projects/LLM-Cockpit/lessons-learned/LL-NNN-*.md`. Lessons that apply equally to the agent platform are **also** filed in `020 Projects/AgenticBlox/lessons-learned/` (or referenced from there, to avoid duplication).

---

## 4. Sprint structure

Same as AgenticBlox PROCESS §4:

- Sprint length: 1 week. Mon → Sun. Review + Retro on Sunday.
- Sprint document: `sprints/sprint-NN.md`.
- Canonical state: `process/SPRINT_STATE.md`.
- Sprint Review checklist (A — Process / B — Design Principles / C — Documentation / D — Lessons Learned) is identical to AgenticBlox PROCESS §4a, with one substitution:

  - In §4a-B, replace "no memory stored in SQLite tables (MD-Primary per the memory system design)" with **"chat history persistence is the only writer of `messages` / `conversations` tables (DP-013)"**. The cockpit's data-model rule is that no other module writes those tables.

---

## 5. Repository layout

| Repo | Content | Branch model |
|------|---------|--------------|
| `Bloxperts/llm-cockpit` | Source code (FastAPI backend, Next.js frontend) and the `/docs` mirror of the vault subset. | `main` + `develop` + `feature/*` (active from day one). |

There is no separate `llm-cockpit-docs` repo. Per ADR-002, design docs are mirrored into `/docs` inside the same repo (`/docs/PROCESS.md`, `/docs/decisions/`, `/docs/design-principles/`, `/docs/specs/`). The vault remains the SoT (DP-024); the mirror is updated at sprint review via `scripts/sync-docs-from-vault.sh`.

---

## 6. Definition of Done (per User Story)

A User Story is Done (technical) when **all** of the following are true:

1. User Spec, Functional Spec, Test Spec all `Accepted`.
2. Functional Spec has a filled-in DG-004 block iff the spec crosses the platform boundary.
3. Implementation commits prefixed `[US-NN] …`, on a `feature/US-NN-*` branch.
4. Test Spec's test cases pass — automated where the Test Spec marks them automated, manual where it marks them manual.
5. JSONL backend logs for the affected routes show no error spike during smoke tests (DP-002).
6. CHANGELOG entry added for the next release.
7. PR opened against `develop` with `US-NN` link in the description.

User Acceptance happens at sprint review. **Chris approves Done → User Accepted explicitly.**

---

## Revision history

- **v1.0 (2026-04-27)** — Initial Accepted version. Establishes inheritance from AgenticBlox PROCESS v2.0 and the four cockpit deltas (three-doc spec form, no agent identity step, develop/main from day one, DG-scope reduced to DG-004).
