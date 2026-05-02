# Status

**Version:** 1.0.0 — public PyPI release **Date:** 2026-05-02

## What runs today

- `cockpit-admin init`, `migrate`, `doctor`, and `serve`.
- Login, forced password change, session controls, chat, code, user admin,
  dashboard live/history, Ollama placement/model lifecycle, model catalog
  pull, performance testing, and admin audit.
- The FastAPI process serves the bundled Next.js static export from
  `src/cockpit/frontend_dist`.

## Current release status

The product is published on PyPI as `llm-cockpit` and can be installed with
`pipx install llm-cockpit`. The public release path has passed:

1. Fresh frontend static export bundled into `src/cockpit/frontend_dist`.
2. Clean wheel + sdist build.
3. `twine check dist/*`.
4. TestPyPI trusted-publisher publish.
5. Production PyPI trusted-publisher publish.
6. Neuroforge install from PyPI plus `cockpit-admin doctor`.

## What you need on Neuroforge to run this

Already in place per `agentic-blox/architecture/DEPLOYMENT-NEUROFORGE.md`:

- ✅ Ollama systemd-managed at port 11434
- ✅ Scheduler systemd-user unit at port 8001
- ✅ `nvidia-powerlimit.service` (320 W / 350 W asymmetric)
- ✅ `ollama-warmup.service` (gemma4:26b + qwen3-coder:30b + embeddinggemma:300m pinned)

The cockpit serves frontend assets from the Python process; no separate
frontend service is required. No sudoers entry is required for the current
release target.
