<!-- Status: Live | Updated: 2026-04-27 -->
# Architecture Decision Records — Index (LLM Cockpit)

ADRs capture architectural decisions made for the cockpit. Format follows the lightweight Michael Nygard shape (Context / Decision / Consequences). Each ADR has a status from `Proposed → Accepted → Deprecated → Superseded`.

| ADR | Title | Status | Supersedes |
|-----|-------|--------|------------|
| [ADR-001](ADR-001-mirror-agentic-blox-process.md) | Mirror AgenticBlox process v2.0 with cockpit deltas | Accepted | — |
| [ADR-002](ADR-002-stack-choices-and-delivery-form.md) | Stack choices and delivery form (FastAPI + Next.js + SQLite + SSE) | Accepted (v1.1) | — |
| [ADR-003](ADR-003-public-release-framing.md) | Public release framing (Ollama-only, pip + CLI, single admin seed) | Accepted | parts of ADR-002 §Stack (scheduler client) |
| [ADR-004](ADR-004-role-ladder.md) | Role ladder + permission model (chat / code / admin) | Accepted | — |

## How an ADR is Accepted

1. Open as `Proposed`.
2. Discuss with Chris.
3. Bump to `Accepted` only with Chris's explicit OK (same rule as PROCESS §2 status flow).
4. On Accept, mirror to `docs/decisions/` in the repo at the next sprint review (PROCESS §8 cadence).
