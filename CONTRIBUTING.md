# Contributing to llm-cockpit

This repo follows the discipline codified in the Obsidian Vault at `020 Projects/LLM-Cockpit/`. The vault is the **source of truth** for design (DP-024); this repo holds the implementation.

If something here disagrees with the vault, the vault wins and the discrepancy is fixed in the repo at the next sprint review.

## Process at a glance

- **Spec-First.** No implementation without an Accepted Functional Spec. See `docs/PROCESS.md`.
- **Three-doc spec form** per User Story: `user/`, `functional/`, `test/` under `docs/specs/` (mirror of vault `specs/`).
- **1-week sprints** Mon → Sun. Sunday review writes a protocol to `docs/process/reviews/SPRINT-NN-REVIEW.md`.
- **Decision Guides** that apply to the cockpit: only **DG-004** (port or adapter) is binding per spec. The other DGs (001/002/003) are agent-shaped and don't apply; DG-003 was decided once for the whole cockpit in `docs/decisions/ADR-002-stack-choices-and-delivery-form.md`.

## Branch model

```
feature/US-NN-short-title          ← all real work happens here
        ↓ (PR after Functional Tests pass)
    develop                        ← test/staging
        ↓ (PR after User Acceptance)
      main                         ← production (Neuroforge :8080)
```

- Always branch from `develop`, never from `main`.
- A PR into `develop` requires green tests + the linked User Story's Functional Spec to be `Accepted` in the vault.
- A PR from `develop` into `main` requires Chris's User Acceptance recorded in the sprint review protocol.
- Tags on `main`: SemVer `vX.Y.Z` (X = architecture, Y = feature, Z = patch).

## Commit messages

Required prefix:

```
[US-NN] short imperative description of what changed
```

Examples:

```
[US-01] add bcrypt password verification + JWT issuance
[US-02] read GPU temp from nvidia-smi via the Telemetry port
[US-07] fail closed when the scheduler returns 5xx
```

Commits without a `[US-NN]` prefix are reviewable but won't merge — the PR template's checklist requires the link.

If a commit applies to **infrastructure / methodology** rather than a story (e.g. CI tweaks, doc sync), prefix with `[chore]`, `[ci]`, or `[docs]`.

## Pull requests

Use the template in `.github/PULL_REQUEST_TEMPLATE.md`. The checklist requires:

- [ ] `[US-NN]` referenced in the title and body.
- [ ] Functional Spec in vault is `Accepted` (paste the link).
- [ ] DG-004 block present iff the spec crosses the platform boundary.
- [ ] Tests pass locally (`pytest` for backend, `npm test` for frontend).
- [ ] CHANGELOG entry added if user-visible.

Reviewer (Chris) checks:

- Does the implementation match the Functional Spec?
- Are the Test Spec's automated cases actually present?
- DP-002 — does the new code log JSONL audit lines for state-changing actions?
- DP-007 — anything we could remove?

## Code style

- **Backend (Python 3.12, FastAPI):** `ruff` + `black` (line length 100). `mypy --strict` on `app/`.
- **Frontend (Next.js + TypeScript):** `eslint` (Next.js preset) + `prettier`. `tsc --noEmit` clean.
- **Tests:** `pytest` for backend (≥ 90 % line coverage on touched modules per the Test Spec), Vitest for frontend logic. Manual UI smoke checks live in the Test Spec, not in the repo.

## Documentation sync from vault

The vault subset (`process/`, `decisions/`, `design-principles/`, `specs/`, `lessons-learned/`) is mirrored into `docs/` in this repo. Sync is **manual at sprint review** by running `scripts/sync-docs-from-vault.sh`. Don't edit `docs/PROCESS.md` or anything under `docs/decisions/` or `docs/specs/` directly in this repo — edit in the vault and re-run the script.

The exceptions are `docs/STATUS.md` and `docs/CONTRIBUTING.md`, which are repo-local.

## Where to ask

- Methodology questions → vault `020 Projects/LLM-Cockpit/process/PROCESS.md` and the AgenticBlox parent.
- DP / ADR questions → vault `020 Projects/LLM-Cockpit/design-principles/DP-INDEX.md` and `decisions/`.
- Operational questions → AgenticBlox `architecture/DEPLOYMENT-NEUROFORGE.md`.

If the answer isn't there, escalate to Chris — and write an LL entry afterwards so the gap is documented.
