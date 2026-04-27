<!-- Status: Accepted | Version: 1.1 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# ADR-002 · Stack choices and delivery form

**Status:** Accepted
**Version:** 1.1
**Date:** 2026-04-27
**Superseded in part by:** ADR-003 §4 (drops the scheduler client) and §1 (adds pip + CLI delivery vehicle).

## Context

The cockpit needs to be opinionated about its own implementation stack so that:

- Specs can refer to concrete technology by name without relitigating per spec.
- DG-003 (delivery form) doesn't need to be re-run for every story; the delivery form is decided once.
- DP-028 (Standard over Invention) has something concrete to point at.

## Decision

### Delivery form (DG-003 verdict — recorded once)

**Web service.** Concretely: a FastAPI backend serving a Next.js frontend at `http://192.168.111.200:8080` on the LAN.

DG-003 was implicitly run when Chris scoped the project (cockpit GOALS.md §primary goals). Verdict: a CLI is insufficient for non-engineering family members; an agent is overkill (the user is the operator); a single web service is the minimum viable form. This decision is **not re-run per spec.** All seven v0.1 user stories share this delivery form.

### Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend language | Python 3.12 | Already the operational language on Neuroforge; simplest integration with `nvidia-smi` subprocess and the scheduler. |
| Backend framework | FastAPI | DP-028 (standard); native ASGI; built-in pydantic; natural SSE support. |
| Backend persistence | SQLite, single file at `/data/cockpit.db` | DP-007 (simplicity); five users + on-host storage means SQLite is the right size. |
| ORM | SQLAlchemy 2.x | Stock, types are good, Alembic for migrations later. |
| Auth | bcrypt + JWT (HS256) in `HttpOnly` `SameSite=Strict` cookie | Lightest production-grade option for a five-user system. No OAuth/SSO/SAML in v0.1 (cockpit GOALS §non-goals). |
| Frontend | Next.js (App Router) + React 18 + TypeScript | DP-028; SSR/streaming + Server Components fit the dashboard well. |
| UI components | shadcn/ui + Tailwind | DP-028 (standard); aligned with how Claude UIs look, which matches the "Claude-shaped" UX goal. |
| Charts | Recharts | DP-028; sufficient for the dashboard scope. |
| Streaming | Server-Sent Events (SSE) over `EventSource` | DP-028; one-way streaming is exactly what chat tokens need; simpler than WebSockets. |
| State (frontend) | TanStack Query for server state, Zustand for UI state | DP-028; minimal footprint. |
| **Distribution (v1.1)** | **Pip-installable wheel `llm-cockpit` containing the FastAPI app + bundled Next.js static build, plus a `cockpit-admin` CLI entry-point.** | **DP-028; ADR-003 §1.** The frontend is built at wheel-build time and served by FastAPI's static-file mount — one process, one port. |
| **CLI (v1.1)** | **`cockpit-admin {init, serve, user-add, user-set-password, user-set-role, user-delete, user-list, migrate}`.** | DP-007; gives headless installs and CI a non-UI path. |
| Process supervision | systemd-user unit (Linux), Docker Compose (cross-platform), or plain `cockpit-admin serve` for dev. | DP-028. |
| Deploy | `pip install --upgrade llm-cockpit` (or rebuild the Docker image). Migrations run automatically on `cockpit-admin serve` startup. | DP-007. |

### Repository topology

- **One repo:** `Bloxperts/llm-cockpit` (private), holding code **and** the `/docs` mirror of the vault subset. No separate `llm-cockpit-docs` repo.
- **Two branches:** `main` (production) and `develop` (test/staging). Feature branches `feature/US-NN-short-title` merge into `develop` after Functional Tests pass; `develop` merges into `main` after User Acceptance.
- **`/docs` mirror** is updated at sprint review by `scripts/sync-docs-from-vault.sh` (PROCESS §8 cadence). Vault is SoT (DP-024).

## Consequences

**Positive**

- Specs can drop "we use FastAPI" / "we use Next.js" boilerplate — the stack is decided.
- DG-003 verdict is recorded; not re-run per story.
- DP-028 has a concrete reference point.
- Topology mirrors AgenticBlox conventions on Neuroforge (`systemd-user`, install script).

**Negative**

- Locking SQLite limits horizontal scaling. Mitigated: cockpit GOALS §non-goals exclude multi-host. Migration to Postgres is an ADR if/when required.
- SSE is one-way. If we ever need bidirectional (e.g. live cursor presence), we'll add WebSockets as a second adapter — not replace SSE.
- Single repo couples docs and code. Mitigated by the per-folder PR template and CONTRIBUTING rules.
- **(v1.1)** Bundling the frontend into the Python wheel adds a build step (`npm run build && next export` or `npm run build` with the standalone output). This is well-trodden but requires CI to have Node available alongside Python. Sprint 1 architecture work owns the exact build recipe.

**Neutral**

- The stack matches `docs/STATUS.md` and the existing scaffold; no rewrite required.

## Compliance

- DP-007 / DP-028 — explicitly applied.
- DP-008 (escape-hatch) — Ollama and vLLM behind the same `LLMChat` port; either is swappable.
- DP-029 — the three ports (LLMChat, SchedulerControl, Telemetry) and their adapters are listed in DP-INDEX cockpit notes; DG-004 enforces.
- DP-024 — vault remains SoT; `/docs` is mirror.

## DG-003 output block (recorded)

> Delivery ladder candidates considered: script · CLI · skill · service (web) · agent.
>
> - **Script** — rejected: a script can sample telemetry but cannot serve a chat UI to humans.
> - **CLI** — rejected: GOALS.md §4 explicitly says non-engineering family members shouldn't have to use a CLI.
> - **Skill** — rejected: skills run inside an agent; cockpit is not an agent.
> - **Service (web)** — **selected.** Smallest delivery form that lets multiple humans on the LAN observe and chat with the local models.
> - **Agent** — rejected per cockpit GOALS §anti-goals ("No agent flows in v0.1").
>
> Verdict: **service (web)**. Verdict-bound DGs that follow (DG-001, DG-002): not run, because the form is not "agent". DG-004 runs per spec on outbound ports.

## Revision history

- **v1.1 (2026-04-27)** — Distribution row added: pip-installable wheel + bundled Next.js + `cockpit-admin` CLI. Process supervision and Deploy rows updated to reflect that. Per ADR-003, the scheduler client referenced in this ADR's earlier text is dropped from v0.1 (`OllamaLLMChat` is the only `LLMChat` adapter).
- **v1.0 (2026-04-27)** — Initial Accepted version. Locked the FastAPI / Next.js / SQLite / SSE stack, recorded the DG-003 verdict (web service).
