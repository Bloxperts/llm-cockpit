<!-- Required prefix in title: [US-NN] short description -->

## What this PR does

(one-paragraph summary of the change)

## User Story

- **US:** US-NN — `<title>`
- **Vault User Spec:** `020 Projects/LLM-Cockpit/specs/user/US-NN-…md` — status: …
- **Vault Functional Spec:** `020 Projects/LLM-Cockpit/specs/functional/US-NN-…md` — status: **must be Accepted**
- **Vault Test Spec:** `020 Projects/LLM-Cockpit/specs/test/US-NN-…md` — status: …

## Process checklist

- [ ] Title contains `[US-NN]`.
- [ ] Functional Spec in the vault is `Accepted`.
- [ ] DG-004 (port or adapter) block present in the Functional Spec **iff** this PR crosses the platform boundary (scheduler / Ollama / vLLM / `nvidia-smi`). N/A is also a valid answer — say which.
- [ ] Test Spec's automated cases are present in this PR.
- [ ] `pytest` (backend) and `npm test` (frontend, if touched) pass locally.
- [ ] DP-002 — every state-changing action emits a JSONL audit line.
- [ ] DP-007 — sanity check: anything that could be deleted?
- [ ] CHANGELOG entry added if the change is user-visible.

## Branch sanity

- [ ] Branch name is `feature/US-NN-short-title`.
- [ ] Target branch is `develop` (not `main`).

## Reviewer notes

(anything Chris should look at first; performance considerations; risky areas)
