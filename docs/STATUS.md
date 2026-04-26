# Status

**Version:** 0.1.0 — scaffold only **Date:** 2026-04-26

## What runs today

- `GET /healthz` returns 200.
- `GET /api/dashboard/snapshot` returns a STUB payload.
- Frontend renders a placeholder landing page.

## What's spec'd but not built

SpecStatusImplementationSPEC-001 loginDraft (vault)not builtSPEC-002 dashboard liveDraft (vault)stub returns `{status: "STUB"}`SPEC-003 dashboard historyDraft (vault)not builtSPEC-004 chat pageDraft (vault)not builtSPEC-005 code pageDraft (vault)not builtSPEC-006 admin controlsDraft (vault)not builtSPEC-007 scheduler routingDraft (vault)scheduler_client module not yet present

## Implementation order (proposed)

1. SPEC-001 + skeleton frontend `/login` page.
2. SPEC-002 dashboard live + telemetry sampler.
3. SPEC-007 scheduler-client (no UI work, just the module + tests).
4. SPEC-004 chat page (depends on 1+7).
5. SPEC-005 code page (alias of chat).
6. SPEC-003 dashboard history.
7. SPEC-006 admin controls (last; touches privileged ops).

## What you need on Neuroforge to run this

Already in place per `agentic-blox/architecture/DEPLOYMENT-NEUROFORGE.md`:

- ✅ Ollama systemd-managed at port 11434
- ✅ Scheduler systemd-user unit at port 8001
- ✅ `nvidia-powerlimit.service` (320 W / 350 W asymmetric)
- ✅ `ollama-warmup.service` (gemma4:26b + qwen3-coder:30b + embeddinggemma:300m pinned)

To-be-added when this project ships v0.1:

- `~/.config/systemd/user/llm-cockpit.service` (the FastAPI backend)
- `~/.config/systemd/user/llm-cockpit-frontend.service` (or serve static via FastAPI)
- `/etc/sudoers.d/llm-cockpit` (per SPEC-006, scoped NOPASSWD for `nvidia-smi -pl` and a few `systemctl` commands)
