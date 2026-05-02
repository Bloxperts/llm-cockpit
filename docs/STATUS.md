# Status

**Version:** 0.5.7 beta — pre-1.0 release hardening **Date:** 2026-05-02

## What runs today

- `cockpit-admin init`, `migrate`, `doctor`, and `serve`.
- Login, forced password change, session controls, chat, code, user admin,
  dashboard live/history, Ollama placement/model lifecycle, model catalog
  pull, performance testing, and admin audit.
- The FastAPI process serves the bundled Next.js static export from
  `src/cockpit/frontend_dist`.

## Current release gate

The product is not yet public-PyPI `v1.0.0`. Remaining release gates:

1. Build the fresh frontend static export into `src/cockpit/frontend_dist`.
2. Build wheel + sdist from a clean tree.
3. Run `twine check dist/*`.
4. Install the local wheel in an isolated environment.
5. Run `cockpit-admin --version`, `cockpit-admin init`, `cockpit-admin serve`,
   and `cockpit-admin doctor` as a smoke.
6. Exercise TestPyPI or document the exact account/trusted-publisher blocker.
7. Publish production PyPI only after Chris explicitly says "go publish PyPI".

## What you need on Neuroforge to run this

Already in place per `agentic-blox/architecture/DEPLOYMENT-NEUROFORGE.md`:

- ✅ Ollama systemd-managed at port 11434
- ✅ Scheduler systemd-user unit at port 8001
- ✅ `nvidia-powerlimit.service` (320 W / 350 W asymmetric)
- ✅ `ollama-warmup.service` (gemma4:26b + qwen3-coder:30b + embeddinggemma:300m pinned)

The cockpit serves frontend assets from the Python process; no separate
frontend service is required. No sudoers entry is required for the current
release target.
