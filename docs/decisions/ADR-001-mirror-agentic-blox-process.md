<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 -->
# ADR-001 · Mirror AgenticBlox process v2.0 with cockpit deltas

**Status:** Accepted
**Date:** 2026-04-27
**Supersedes:** —

## Context

The cockpit is a sister project to AgenticBlox. Both are owned by Chris. AgenticBlox already runs a mature design-first methodology codified in `020 Projects/AgenticBlox/process/PROCESS.md` v2.0 (Accepted 2026-04-22): Spec-First, 1-week sprints, Sunday review with checklist A–D, Decision Guides binding on design docs, SemVer + git-centred governance.

The cockpit could in principle pick a lighter or different process. The two main alternatives considered:

1. **Lighter process** — Drop the three-doc spec form; combine User / Functional / Test in one file. Drop sprint reviews.
2. **Different process** — e.g. trunk-based + ADRs only, no sprint cadence.

The cockpit is small (~7 user stories for v0.1) but it is **not throwaway**: it sits in production on Neuroforge, four humans + testers depend on it, and it co-evolves with AgenticBlox. A lighter process would create friction every time we want to share a lesson, an ADR, or a DP between the two projects. A different process would force Chris to context-switch.

## Decision

The cockpit adopts AgenticBlox PROCESS v2.0 verbatim, with **four documented deltas** (recorded in cockpit `process/PROCESS.md` §3):

1. **Three-doc spec form mandatory** from day one (`specs/user/`, `specs/functional/`, `specs/test/`).
2. **PROCESS §9 (agent identity step) does not apply** — the cockpit ships no agents.
3. **`develop` / `main` two-branch flow active from day one** (the AgenticBlox runtime is still single-branch until Block 4; the cockpit runtime exists today).
4. **DG scope reduced to DG-004** — DG-001 / DG-002 / DG-003 are not binding per spec (logged once in ADR-002 for delivery form). DG-004 (port or adapter) is binding on every Functional Spec that crosses the platform boundary.

Design Principles are inherited from AgenticBlox by reference per `design-principles/DP-INDEX.md` v1.0; no cockpit-local DPs at this stage.

## Consequences

**Positive**

- Zero cognitive switching cost between the two projects.
- Lessons learned, ADR conventions, sprint review checklist, git-flow conventions are reusable verbatim.
- Status-flow gatekeeping (Review→Accepted, Done→User Accepted) keeps Chris in the loop at exactly the same checkpoints as AgenticBlox.
- The cockpit becomes a stress-test for the methodology — anything that breaks here is a signal to refine the AgenticBlox PROCESS too.

**Negative**

- Three-doc spec form is heavier than a single SPEC-NNN file. Mitigated by the cockpit's small scope (~7 stories for v0.1). For trivial stories the User Spec and Test Spec can be very short.
- The cockpit must keep the inheritance link explicit. If AgenticBlox PROCESS bumps SemVer, the cockpit must check whether the change requires a delta update here.

**Neutral**

- The seven historical `SPEC-NNN-*.md` drafts must be migrated into the three-doc layout. Done in Sprint 0 as part of the methodology bootstrap; no functional change.

## Compliance

- DP-017 (Design before Implementation) — satisfied.
- DP-022 (Documentation as First-Class) — satisfied (this ADR is the doc).
- DP-023 (Git-centred Change Governance) — satisfied (develop/main from day one).
- DP-024 (Vault as SoT) — satisfied (canonical location is the vault; repo `/docs` is mirror).
