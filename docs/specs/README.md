<!-- Status: Live | Updated: 2026-04-27 -->
# Specs — Index (LLM Cockpit)

Per PROCESS §3 / ADR-001, every User Story has three artefacts:

- `user/US-NN-short-title.md` — User Specification (what the user wants).
- `functional/US-NN-short-title.md` — Functional Specification (how the system delivers it).
- `test/US-NN-short-title.md` — Test Specification (how it is verified).

| US | Title | Min role | Sprint | User | Functional | Test |
|----|-------|----------|--------|------|------------|------|
| US-01 | User logs in to the cockpit | any | 2 | [user](user/US-01-login.md) — Review | [functional](functional/US-01-login.md) — Review | [test](test/US-01-login.md) — Review |
| US-02 | Live dashboard (Ollama + optional GPU) | any (filtered by role) | 3 | [user](user/US-02-dashboard-live.md) — Review | [functional](functional/US-02-dashboard-live.md) — Review | [test](test/US-02-dashboard-live.md) — Draft |
| US-03 | Dashboard history (24 h, 7 d) | any (filtered by role) | 5 | [user](user/US-03-dashboard-history.md) — Review | [functional](functional/US-03-dashboard-history.md) — Review | [test](test/US-03-dashboard-history.md) — Draft |
| US-04 | Chat interface (chat-tagged models) | `chat` | 4 | [user](user/US-04-chat-page.md) — Review | [functional](functional/US-04-chat-page.md) — Review | [test](test/US-04-chat-page.md) — Draft |
| US-05 | Code interface (code-tagged models) | `code` | 4 | [user](user/US-05-code-page.md) — Review | [functional](functional/US-05-code-page.md) — Review | [test](test/US-05-code-page.md) — Draft |
| US-06 | Admin: User management | `admin` | 6 | [user](user/US-06-admin-controls.md) — Review | [functional](functional/US-06-admin-controls.md) — Review | [test](test/US-06-admin-controls.md) — Draft |
| US-07 | Ollama integration (`LLMChat` port) | infra | 1 (design) → 2 (build) | [user](user/US-07-scheduler-routing.md) — Review | [functional](functional/US-07-scheduler-routing.md) — Review | [test](test/US-07-scheduler-routing.md) — Draft |
| US-08 | First-run installation + bootstrap | n/a (pre-user) | 2 | [user](user/US-08-installation-bootstrap.md) — Review | [functional](functional/US-08-installation-bootstrap.md) — Review | [test](test/US-08-installation-bootstrap.md) — Review |
| US-09 | First-login forced password change | any | 2 | [user](user/US-09-first-login-password-change.md) — Review | [functional](functional/US-09-first-login-password-change.md) — Review | [test](test/US-09-first-login-password-change.md) — Review |
| US-10 | Admin: Ollama configuration + metrics | `admin` | 7 | [user](user/US-10-ollama-configuration.md) — Review | [functional](functional/US-10-ollama-configuration.md) — Review | [test](test/US-10-ollama-configuration.md) — Draft |

## Filename note

US-06 (`admin-controls`) and US-07 (`scheduler-routing`) keep their original filenames for stability. The titles inside have changed to "User management" (US-06, per ADR-003 §6) and "Ollama integration" (US-07, per ADR-003 §4). Filename rename is a possible v0.2 cleanup.

## Status legend

`Draft → Review → Accepted → In Progress → Done → User Accepted` (PROCESS §2). Implementation may not begin until the **Functional Spec** is `Accepted`.

## Decision Guide bindings

DG-004 (port or adapter) is **binding** on:

- **US-02** — `Telemetry` and `LLMChat` ports.
- **US-07** — `LLMChat` port (the canonical block).
- **US-10** — extends `LLMChat` with `pull_model` / `delete_model`.

US-04 / US-05 inherit US-07's block (no new boundary surface). US-01 / US-03 / US-06 / US-08 / US-09 do not cross the platform boundary in v0.1 — DG-004 is N/A.

DG-001 / DG-002 / DG-003 do not apply (no agents; delivery form locked once in ADR-002).

## Min-role table

Role gating per ADR-004:

| Min role | Stories |
|----------|---------|
| any (logged in) | US-01, US-02 (filtered view), US-03 (filtered view), US-09 |
| `chat` | US-04 |
| `code` | US-05 |
| `admin` | US-06, US-10 |
| infra (no user) | US-07 |
| n/a (pre-user) | US-08 |
