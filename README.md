# llm-cockpit

A local dashboard + chat UI for the Neuroforge 2.0 LLM serving stack.

> **Source of truth for design:** the obsidian vault under `020 Projects/LLM-Cockpit/`.
> This repository holds the implementation; design docs live in the vault.

## What this is

Two pages backed by one FastAPI service running on Neuroforge:

1. **Dashboard** — live + historical observability for the Ollama / vLLM serving stack: loaded models, GPU temps and VRAM, scheduler queue stats, per-call token distributions.
2. **Chat / Code** — Claude-shaped UI for direct conversations with `gemma4:26b`, `qwen3-coder:30b`, and on-demand `deepseek-r1:32b` / `qwen2.5-72B-AWQ`. Multi-user with simple bcrypt password login.

Both pages route every LLM call through the existing queue layer (`scheduler` on port 8001) so single-flight semantics for heavy slots are enforced uniformly.

## Status

**v0.1 — scaffolding.** Specs Accepted in vault are gradually being implemented. See `docs/STATUS.md` for what works and what doesn't.

## Quick start (development on a Mac)

```bash
# clone
git clone git@github.com:Bloxperts/llm-cockpit.git
cd llm-cockpit

# backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8080

# frontend (separate terminal)
cd ../frontend
npm install
npm run dev      # http://localhost:3000 in dev, talks to backend on 8080
```

## Quick start (production on Neuroforge)

```bash
# on Neuroforge:
git clone git@github.com:Bloxperts/llm-cockpit.git ~/llm-cockpit
cd ~/llm-cockpit
./scripts/install.sh    # creates .venv, installs, builds frontend, sets up systemd-user units
systemctl --user start llm-cockpit
# UI at http://192.168.111.200:8080
```

## Repo layout

```
backend/                   FastAPI (Python 3.12)
  app/
    main.py                lifecycle + routers
    auth.py                bcrypt + JWT
    db.py                  SQLite engine
    models.py              SQLAlchemy ORM
    schemas.py             Pydantic
    scheduler_client.py    talks to scheduler:8001
    ollama_client.py       talks to ollama:11434 (admin only)
    telemetry.py           nvidia-smi sampler
    routers/
      auth.py
      dashboard.py
      chat.py
      code.py
      admin.py
  cli.py                   cockpit-admin CLI
  pyproject.toml
frontend/                  Next.js (App Router) + shadcn
  app/
    (auth)/login/
    (app)/dashboard/
    (app)/chat/
    (app)/code/
    (app)/admin/
    layout.tsx
  lib/
    api.ts
    sse.ts
  components/
    ui/                    shadcn drop-in
    chart/
    chat/
docs/
  STATUS.md
  CONTRIBUTING.md
scripts/
  install.sh               first-run setup on Neuroforge
.env.example
docker-compose.yml         optional alternative to systemd
```

## Process

This repo follows the same `Draft → Review → Accepted → Deploy → Archived` discipline as `agentic-blox`. Specs in the vault must be `Accepted` before code lands here. ADRs go in the vault under `decisions/`.

## License

Private. Bloxperts internal use only for now.
