# AGENTS.md — llm-cockpit

This file is loaded by Codex at the start of every session. Read it first; defer to the linked docs for detail. The full design lives in **`docs/`** in this repo, mirrored from the Obsidian vault at `020 Projects/LLM-Cockpit/` (the vault is the source of truth — `docs/` follows the vault, not the other way round).

## What this project is

A multi-user web interface for [Ollama](https://ollama.com): a dashboard + chat / code UI you can `pip install` and have running in five minutes. Public open-source. Local-first. Single Python process serving FastAPI + a bundled Next.js static frontend, talking to one Ollama daemon.

Primary architectural docs:

- `docs/architecture/COMPONENTS.md` — component map, ports, data model, deployment shapes.
- `docs/decisions/ADR-INDEX.md` and the five ADRs.
- `docs/process/PROCESS.md` — the methodology.
- `docs/use-cases/README.md` — the ten use cases that scope v0.1.

## The one rule that overrides everything

**Spec-First.** No implementation without an Accepted Functional Spec. Status flow:

```
Draft → Review → Accepted → In Progress → Done → User Accepted
```

`Review→Accepted` and `Done→User Accepted` always require **Chris's explicit OK**. Never advance those gates yourself.

Before writing code for a use case, **read its three documents** in this order:

1. `docs/use-cases/UC-NN-*.md` — what the user wants.
2. `docs/specs/functional/UC-NN-*.md` — how the system delivers it. Must be `Status: Accepted`.
3. `docs/specs/test/UC-NN-*.md` — how it's verified.

If the Functional Spec is not Accepted, **stop and tell Chris**. Don't implement against a Draft / Review spec.

## Branch + commit conventions (ADR-001, mirrored from AgenticBlox PROCESS v2.0)

```
feature/UC-NN-short-title
        ↓ (PR after Functional Tests pass)
    develop
        ↓ (PR after User Acceptance)
      main                            ← production
```

- Always branch from `develop`. Never from `main`.
- Commit prefix: `[UC-NN] short imperative description`. Methodology / chore commits use `[chore]`, `[ci]`, `[docs]`.
- One PR per use case. PR template at `.github/PULL_REQUEST_TEMPLATE.md` is mandatory; the checklist requires the spec link, the DG-004 block (when relevant), and tests.
- SemVer tags on `main`: `vX.Y.Z` (X = architecture, Y = feature, Z = patch).

## Stack (ADR-002 v1.1)

| Layer | Choice |
|-------|--------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite at `$COCKPIT_DATA_DIR/cockpit.db` |
| Frontend | Next.js (App Router) + TypeScript + shadcn/ui + Tailwind, **static export bundled into the Python wheel** |
| Streaming | SSE via native `EventSource` |
| Auth | bcrypt + JWT (HS256) in `HttpOnly`, `SameSite=Strict` cookie |
| State (frontend) | TanStack Query for server state, Zustand for UI state |
| Distribution | Pip-installable wheel `llm-cockpit` + `cockpit-admin` CLI |

`pyproject.toml` registers the CLI:

```toml
[project.scripts]
cockpit-admin = "cockpit.cli:main"
```

Use stock components (DP-028). Don't invent.

## Architecture (ADR-003 + ADR-004 + ADR-005)

- **Two ports:** `LLMChat` (outbound to Ollama, also covers pull/delete) and `Telemetry` (optional outbound to `nvidia-smi`, `sample()` returns `None` when absent).
- **One adapter per port in v0.1:** `OllamaLLMChat` and `NvidiaSmiTelemetry`. Plus `FakeLLMChat` and `FakeTelemetry` for tests.
- **No scheduler / queue layer.** The cockpit talks to Ollama directly. Single-flight is enforced **inside the cockpit** via per-model `asyncio.Lock` (ADR-005 §5).
- **No code outside `adapters/` may import an external SDK or open a socket.** Routers and services depend on ports.

## Roles (ADR-004)

Three-rung ladder: `chat < code < admin`. One role per user. Stored in `users.role`. JWT carries only `sub`; role is resolved per-request from the database so admin role flips take effect immediately.

```python
def require_role(min_role: Role):
    rank = {"chat": 0, "code": 1, "admin": 2}
    def dep(user: User = Depends(current_user)):
        if rank[user.role] < rank[min_role]:
            raise HTTPException(403, "insufficient role")
        return user
    return dep
```

The `require_role(min_role)` dependency is the **single** authorization gate. Frontend hides menus by role too, but that's cosmetic — the backend is the real enforcement point.

Bootstrap seeds **one** account: `admin` / `ollama` / role `admin` / `must_change_password=true`. UC-09 enforces the forced change.

## Per-model lifecycle (ADR-005, in v0.1 scope from Sprint 3 on)

- `model_config(model PK, placement, keep_alive_seconds, num_ctx_default, single_flight, notes)`.
- `model_perf(...)` for the performance-harness history.
- Placement board on UC-02 (Sprint 3) is admin drag-and-drop across `gpu0..N`, `multi_gpu`, `on_demand`, `available`.
- Cockpit pushes `keep_alive` / `main_gpu` / `num_gpu` per model on every chat call. Hard per-GPU pinning is best-effort (Ollama limit); the dashboard shows requested-vs-actual placement honestly.
- Per-model `asyncio.Lock` enforces `single_flight=true`.
- "Test performance" harness (ADR-005 §4) runs cold-load + throughput + max-ctx probe.

## Directory layout (target — to be created in Sprint 2)

```
src/cockpit/
├── cli.py                ← cockpit-admin entry point
├── main.py               ← FastAPI app
├── config.py             ← Pydantic Settings
├── db.py                 ← SQLAlchemy engine + session
├── ports/
│   ├── llm_chat.py
│   └── telemetry.py
├── adapters/
│   ├── ollama_chat.py
│   ├── telemetry.py
│   ├── fake_chat.py
│   └── fake_telemetry.py
├── routers/
│   ├── auth.py
│   ├── dashboard.py
│   ├── chat.py
│   ├── code.py
│   ├── admin_users.py
│   └── admin_ollama.py
├── services/
│   ├── users.py
│   ├── model_tags.py
│   ├── metrics.py
│   ├── audit.py
│   └── settings.py
├── models.py             ← SQLAlchemy ORM
├── schemas.py            ← Pydantic
├── migrations/           ← alembic versions
├── frontend_dist/        ← bundled Next.js static export (built at wheel-build time)
└── default_config/
    ├── model_tag_heuristics.yaml
    └── code_default_system_prompt.md

frontend/                 ← Next.js sources; `npm run build` produces frontend_dist/
docs/                     ← vault mirror; do not edit directly
scripts/sync-docs-from-vault.sh
```

## Decision Guides

Of the four AgenticBlox DGs, only **DG-004** (port or adapter) binds the cockpit. Every Functional Spec that crosses the platform boundary carries a filled-in DG-004 block. Currently bound: UC-02, UC-07, UC-10. UC-04 and UC-05 inherit UC-07's block.

DG-001 / DG-002 / DG-003 don't apply (no agents; delivery form locked once in ADR-002).

## Testing discipline

- Backend: `pytest` + `httpx`, in-process `TestClient`. Aim ≥ 90 % line coverage on touched modules per Test Spec.
- `FakeLLMChat` and `FakeTelemetry` are the test seams — never reach a real Ollama or GPU in unit tests.
- A `pytest -m integration` suite runs against a real Ollama; **not required** for `develop` merges.
- Wire-shape contract test pins the exact NDJSON keys the cockpit reads from Ollama. If a major Ollama bump breaks it, fail loudly there before any user-visible regression.
- Frontend: Vitest for logic; manual UI smoke at sprint review per the Test Spec.

## When to stop and ask vs. proceed autonomously

Stop and ask Chris when:

- A Functional Spec doesn't yet exist or isn't `Accepted` for the work you're about to do.
- The DG-004 block is missing from a Functional Spec that crosses the boundary.
- You discover the spec is **wrong** about something that matters (e.g. an Ollama API doesn't behave as documented). Document the gap, propose a fix to the spec, ask Chris to accept the spec change before continuing.
- You're about to introduce an ADR-level decision (new dependency, new boundary, new port, schema change beyond what's specced).
- You're about to make a state-changing change on `main`.

Proceed autonomously when:

- Work is bounded by an Accepted Functional Spec + Test Spec.
- You're refactoring without behaviour change inside one PR.
- You're adding tests for already-implemented behaviour.

## Current sprint

`docs/process/SPRINT_STATE.md` is the live state. **Read it at the start of every session.** It tells you which sprint is open, which UCs are in scope, and which are explicitly out.

As of 2026-04-27, **Sprint 2** is open: build UC-08 (installer) → UC-07 (LLMChat port partial) → UC-09 (first-login change) → UC-01 (login). End-of-sprint smoke: clean install, login, change password, empty dashboard placeholder.

## What this project deliberately does **not** include

(Don't drift here, even if it seems easy.)

- No upstream queue layer / scheduler. Single-flight is in-process (ADR-005 §5).
- No pluggable `LLMChat` adapter mechanism in v0.1 (only `OllamaLLMChat`). v0.2.
- No external identity provider. Bcrypt + JWT cookie only.
- No multi-host topology. One cockpit, one Ollama, one SQLite.
- No password reset over email. Admin reset is the only path in v0.1.
- No HTTPS / TLS in the cockpit itself. LAN-only by default; reverse-proxy / VPN at the operator's discretion.
- No native mobile clients in v0.1. iPhone-over-VPN is a v2 idea.

## Source of truth and `docs/` sync

The vault at `~/Documents/.../020 Projects/LLM-Cockpit/` (when mounted) is the **source of truth** for design (DP-024). The `docs/` folder is a one-way mirror, refreshed at sprint review by `scripts/sync-docs-from-vault.sh`. **Never edit `docs/` directly** — change the vault, then re-run the sync.

If the vault isn't mounted on this machine, treat `docs/` as authoritative for this session and flag the divergence to Chris at the end.

## Reading list before any non-trivial change

1. This file.
2. `docs/process/SPRINT_STATE.md` (current sprint).
3. `docs/process/PROCESS.md` (canonical methodology).
4. The Use Case + Functional Spec + Test Spec for the UC you're touching.
5. `docs/architecture/COMPONENTS.md` if your change touches the architecture.
6. The ADRs that govern the area (`docs/decisions/`).
