<!-- Required prefix in title: [UC-NN] short description -->

## What this PR does

(one-paragraph summary of the change)

## Use Case

- **UC:** UC-NN — `<title>`
- **Vault Use Case:** `020 Projects/LLM-Cockpit/use-cases/UC-NN-…md` — status: …
- **Vault Functional Spec:** `020 Projects/LLM-Cockpit/specs/functional/UC-NN-…md` — status: **must be Accepted**
- **Vault Test Spec:** `020 Projects/LLM-Cockpit/specs/test/UC-NN-…md` — status: …

## Process checklist

- [ ] Title contains `[UC-NN]`.
- [ ] Functional Spec in the vault is `Accepted`.
- [ ] DG-004 (port or adapter) block present in the Functional Spec **iff** this PR crosses the platform boundary (Ollama / `nvidia-smi`). N/A is also a valid answer — say which.
- [ ] Test Spec's automated cases are present in this PR.
- [ ] `pytest` (backend) and `npm test` (frontend, if touched) pass locally.
- [ ] DP-002 — every state-changing action emits a JSONL audit line.
- [ ] DP-007 — sanity check: anything that could be deleted?
- [ ] CHANGELOG entry added if the change is user-visible.

## Branch sanity

- [ ] Branch name is `feature/UC-NN-short-title` (or a `[chore]`/`[ci]`/`[docs]` branch).
- [ ] Target branch is `develop` (not `main`).

## Reviewer notes

(anything Chris should look at first; performance considerations; risky areas)
