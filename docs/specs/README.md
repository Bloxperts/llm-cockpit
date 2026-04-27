<!-- Status: Live | Updated: 2026-04-27 -->
# Specs — Index (LLM Cockpit)

Per PROCESS §3 / ADR-001, every User Story has three artefacts:

- `user/US-NN-short-title.md` — User Specification (what the user wants).
- `functional/US-NN-short-title.md` — Functional Specification (how the system delivers it).
- `test/US-NN-short-title.md` — Test Specification (how it is verified).

| US | Title | Page | User | Functional | Test | Sprint |
|----|-------|------|------|------------|------|--------|
| US-01 | Family member logs in to the cockpit | login | [user](user/US-01-login.md) | [functional](functional/US-01-login.md) | [test](test/US-01-login.md) | 1 |
| US-02 | Operator sees live model + GPU + queue state | dashboard | [user](user/US-02-dashboard-live.md) | [functional](functional/US-02-dashboard-live.md) | [test](test/US-02-dashboard-live.md) | 2 |
| US-03 | Operator sees historical metrics (24 h, 7 d) | dashboard | [user](user/US-03-dashboard-history.md) | [functional](functional/US-03-dashboard-history.md) | [test](test/US-03-dashboard-history.md) | 6 |
| US-04 | User chats with the local orchestrator (gemma4:26b) | chat | [user](user/US-04-chat-page.md) | [functional](functional/US-04-chat-page.md) | [test](test/US-04-chat-page.md) | 4 |
| US-05 | User uses the coder model (qwen3-coder:30b) | code | [user](user/US-05-code-page.md) | [functional](functional/US-05-code-page.md) | [test](test/US-05-code-page.md) | 5 |
| US-06 | Admin pins/unpins models, adjusts num_ctx | admin | [user](user/US-06-admin-controls.md) | [functional](functional/US-06-admin-controls.md) | [test](test/US-06-admin-controls.md) | 7 |
| US-07 | All chat / code calls go through the scheduler queue | infra | [user](user/US-07-scheduler-routing.md) | [functional](functional/US-07-scheduler-routing.md) | [test](test/US-07-scheduler-routing.md) | 3 |

## Status legend

`Draft → Review → Accepted → In Progress → Done → User Accepted` (PROCESS §2).

Each spec carries its own status header. Implementation may not begin until the **Functional Spec** is `Accepted`.

## DG-004 binding

The Functional Spec for US-02, US-06, and US-07 must include a filled-in DG-004 (port or adapter) block — they all cross the platform boundary to the scheduler, Ollama, or `nvidia-smi`. See ADR-001 §4.
