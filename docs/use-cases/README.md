<!-- Status: Live | Updated: 2026-04-27 -->
# Use Cases — Index (LLM Cockpit)

The **use cases** here describe what the cockpit does, from the user's perspective. Each Use Case is paired with a Functional Spec (`../specs/functional/UC-NN-*.md`) and a Test Spec (`../specs/test/UC-NN-*.md`); together they form the three-doc form per PROCESS §3 / ADR-001.

This is the cockpit-local equivalent of AgenticBlox's `use-cases/` folder.

| UC | Title | Min role | Sprint | Use Case | Functional | Test |
|----|-------|----------|--------|----------|------------|------|
| UC-01 | User logs in to the cockpit | any | 2 | [uc](UC-01-login.md) | [fn](../specs/functional/UC-01-login.md) | [t](../specs/test/UC-01-login.md) |
| UC-02 | Live dashboard + model placement board | any (filtered by role) | 3 | [uc](UC-02-dashboard-live.md) | [fn](../specs/functional/UC-02-dashboard-live.md) | [t](../specs/test/UC-02-dashboard-live.md) |
| UC-03 | Dashboard history (24 h, 7 d) | any (filtered by role) | 5 | [uc](UC-03-dashboard-history.md) | [fn](../specs/functional/UC-03-dashboard-history.md) | [t](../specs/test/UC-03-dashboard-history.md) |
| UC-04 | Chat interface (chat-tagged models) | `chat` | 4 | [uc](UC-04-chat-page.md) | [fn](../specs/functional/UC-04-chat-page.md) | [t](../specs/test/UC-04-chat-page.md) |
| UC-05 | Code interface (code-tagged models) | `code` | 4 | [uc](UC-05-code-page.md) | [fn](../specs/functional/UC-05-code-page.md) | [t](../specs/test/UC-05-code-page.md) |
| UC-06 | Admin: User management | `admin` | 6 | [uc](UC-06-admin-controls.md) | [fn](../specs/functional/UC-06-admin-controls.md) | [t](../specs/test/UC-06-admin-controls.md) |
| UC-07 | Ollama integration (`LLMChat` port) | infra | 1 (design) → 2 (build) | [uc](UC-07-scheduler-routing.md) | [fn](../specs/functional/UC-07-scheduler-routing.md) | [t](../specs/test/UC-07-scheduler-routing.md) |
| UC-08 | First-run installation + bootstrap | n/a (pre-user) | 2 | [uc](UC-08-installation-bootstrap.md) | [fn](../specs/functional/UC-08-installation-bootstrap.md) | [t](../specs/test/UC-08-installation-bootstrap.md) |
| UC-09 | First-login forced password change | any | 2 | [uc](UC-09-first-login-password-change.md) | [fn](../specs/functional/UC-09-first-login-password-change.md) | [t](../specs/test/UC-09-first-login-password-change.md) |
| UC-10 | Admin: Ollama configuration + metrics | `admin` | 7 | [uc](UC-10-ollama-configuration.md) | [fn](../specs/functional/UC-10-ollama-configuration.md) | [t](../specs/test/UC-10-ollama-configuration.md) |
| UC-11 | Public PyPI publishing | n/a (release engineering) | 12 | [uc](UC-11-pypi-publish.md) | [fn](../specs/functional/UC-11-pypi-publish.md) | [t](../specs/test/UC-11-pypi-publish.md) |
| UC-12 | UI refresh and interaction polish | all logged-in roles | 11 | [uc](UC-12-ui-refresh.md) | [fn](../specs/functional/UC-12-ui-refresh.md) | [t](../specs/test/UC-12-ui-refresh.md) |

## Filename note

UC-06's filename retained `admin-controls.md` and UC-07's retained `scheduler-routing.md` from earlier drafts; their internal titles are "User management" (UC-06, per ADR-003 §6) and "Ollama integration" (UC-07, per ADR-003 §4). Filenames may be cleaned up in a v0.2 housekeeping pass.

## Status legend

`Draft → Review → Accepted → In Progress → Done → User Accepted` (PROCESS §2). Implementation may not begin until the **Functional Spec** is `Accepted`.

## Decision Guide bindings

DG-004 (port or adapter) is **binding** on:

- **UC-02** — `Telemetry` and `LLMChat` ports.
- **UC-07** — `LLMChat` port (the canonical block).
- **UC-10** — extends `LLMChat` with `pull_model` / `delete_model`.

UC-04 / UC-05 inherit UC-07's block (no new boundary surface). UC-01 / UC-03 / UC-06 / UC-08 / UC-09 / UC-11 / UC-12 do not cross the runtime platform boundary in v0.1 — DG-004 is N/A.

DG-001 / DG-002 / DG-003 do not apply (no agents; delivery form locked once in ADR-002).

## Min-role table

Role gating per ADR-004:

| Min role | Use cases |
|----------|-----------|
| any (logged in) | UC-01, UC-02 (filtered view), UC-03 (filtered view), UC-09, UC-12 |
| `chat` | UC-04 |
| `code` | UC-05 |
| `admin` | UC-06, UC-10 |
| infra (no user) | UC-07 |
| n/a (pre-user / release engineering) | UC-08, UC-11 |
