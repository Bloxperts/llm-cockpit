# llm-cockpit

A local-first, multi-user web interface for [Ollama](https://ollama.com): a dashboard for what's loaded and how it's behaving, plus a Claude-shaped chat / code UI you can `pip install` and have running in five minutes.

The cockpit assumes you already have Ollama running. It does not install, manage, or supervise Ollama — it talks to it.

## Status

**v0.1 — design phase.** Sprint 0 (methodology bootstrap) and Sprint 1 (architecture) are landing now. Sprint 2 begins implementation: installation, login, first-login password change. See `docs/process/SPRINT_STATE.md` and `docs/STATUS.md`.

## What it does

- **Dashboard.** What models Ollama is serving and which are currently loaded, recent calls with token counts and latency, optional GPU panel (when `nvidia-smi` is on PATH), Ollama-reachability badge.
- **Chat.** Pick any chat-tagged model from your Ollama install and have a streaming conversation. Per-user history, per-conversation system prompt, code-block highlighting.
- **Code.** Same shell as Chat, filtered to code-tagged models, with a coder-default system prompt and diff rendering.
- **Admin (user management).** Add / delete users, set roles on a `chat < code < admin` ladder, reset passwords. Force first-login password change for any seeded or admin-created account.
- **Admin (Ollama configuration).** Tag models `chat` / `code` / `both`, pull / delete models, edit the code-mode default system prompt, see per-model metrics + the audit log.

## Quick start (when v0.1 ships)

```bash
# 1. Have Ollama running (https://ollama.com/download)
ollama serve   # or: systemctl --user start ollama

# 2. Install the cockpit
pipx install llm-cockpit          # or: pip install llm-cockpit (in a venv)

# 3. Bootstrap (probes Ollama, creates admin / ollama, sets must_change_password)
cockpit-admin init

# 4. Run
cockpit-admin serve

# 5. Open http://localhost:8080  → log in as admin / ollama → change password → use.
```

Other supported shapes:

- `docker compose up -d` from the published `compose.yml`.
- `cockpit-admin systemd-install` on Linux for a `~/.config/systemd/user/llm-cockpit.service` unit.

## Roles (ADR-004)

Each user has one role on a ladder. Higher roles include lower-rung capabilities.

| Role | What it can do |
|------|----------------|
| `chat` | Log in, chat with chat-tagged models, see own conversations, change own password. |
| `code` | Above + code with code-tagged models, see own code conversations. |
| `admin` | Above + manage users, configure Ollama (tags, pull/delete, defaults), see system-wide metrics + audit log. |

Bootstrap seeds one user: `admin` / `ollama` with a forced password change on first login.

## Repo layout

```
src/cockpit/                 Python package (planned shape per ADR-002 v1.1)
├── cli.py                   cockpit-admin entry point
├── main.py                  FastAPI app
├── routers/                 auth, dashboard, chat, code, admin_users, admin_ollama
├── services/                users, model_tags, metrics, audit, settings
├── ports/                   LLMChat, Telemetry        (hexagonal)
├── adapters/                ollama_chat, telemetry, fake_chat, fake_telemetry
├── models.py / schemas.py
├── migrations/              alembic
├── frontend_dist/           built Next.js static export, bundled at wheel-build time
└── default_config/          model_tag_heuristics.yaml, code_default_system_prompt.md
docs/                        mirror of the vault subset (synced at sprint review)
├── PROCESS.md, SPRINT_STATE.md
├── decisions/               ADR-001..004
├── design-principles/       DP-INDEX (inherits from AgenticBlox)
├── specs/{user,functional,test}/  US-01..US-10
├── architecture/COMPONENTS.md
└── STATUS.md
scripts/sync-docs-from-vault.sh
```

## Documentation

| Where | What |
|-------|------|
| `docs/PROCESS.md` | Spec-First + 1-week-sprint discipline. |
| `docs/architecture/COMPONENTS.md` | Component map + the two ports (`LLMChat`, `Telemetry`). |
| `docs/decisions/` | ADRs. ADR-001 process; ADR-002 stack; ADR-003 public framing; ADR-004 role ladder. |
| `docs/design-principles/DP-INDEX.md` | Which AgenticBlox DPs we adopt, defer, or skip. |
| `docs/specs/` | One folder per spec type (user / functional / test). |

## Process

Vault is the source of truth (DP-024); `docs/` is the mirror, updated at sprint review by `scripts/sync-docs-from-vault.sh`.

Status flow `Draft → Review → Accepted → In Progress → Done → User Accepted`. Implementation only starts on a Functional Spec at status `Accepted`. `Review→Accepted` and `Done→User Accepted` always require explicit owner approval.

Branches: `feature/US-NN-short-title` → `develop` → `main`. Commit prefix: `[US-NN] short description`.

## License

To be decided before public release. Currently private (Bloxperts internal during pre-release).

## Project home

This repo is the implementation. The design source-of-truth is the project hub in the Obsidian vault at `020 Projects/LLM-Cockpit/`.
