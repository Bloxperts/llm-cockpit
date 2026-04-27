<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# Design Principles — Index (LLM Cockpit)

**Status:** Accepted
**Version:** 1.0
**Date:** 2026-04-27

The cockpit **inherits** the AgenticBlox Design Principles by reference rather than forking them. The canonical DP catalogue is `020 Projects/AgenticBlox/design-principles/` (DP-INDEX v1.4 Accepted).

This index maps every AgenticBlox DP to one of three buckets:

- **Adopt** — binding for the cockpit. Treat the AgenticBlox DP file as the canonical text.
- **Defer** — not relevant for v0.1 of the cockpit, but may apply later. Document the defer reason; revisit at v0.2.
- **Skip** — agent-only concern with no surface in the cockpit.

When an AgenticBlox DP is updated (SemVer bump per DP-018), the cockpit **inherits the update** without re-Accepting it locally — unless the new version meaningfully changes how it applies here, in which case a cockpit-local ADR records the divergence.

Cockpit-specific DPs may eventually be added under `cockpit-DP-NNN-*.md`. None exist as of v1.0.

---

## Operational

| AgenticBlox DP | Title | Bucket | Cockpit interpretation |
|---|---|---|---|
| [DP-001](../../AgenticBlox/design-principles/DP-001-monitoring-architecture.md) | Monitoring Architecture | **Adopt** | The cockpit *is* observability. Backend exposes its own health + metrics endpoints. |
| [DP-002](../../AgenticBlox/design-principles/DP-002-debuggability-transparency.md) | Debuggability & Transparency | **Adopt** | Every backend route logs a structured JSONL line. Login, model controls, and SSE streams all carry an audit trail. |
| [DP-006](../../AgenticBlox/design-principles/DP-006-no-blackbox-agent-behavior.md) | No Black-Box Agent Behaviour | Skip | Cockpit ships no agents. |
| [DP-016](../../AgenticBlox/design-principles/DP-016-evaluation-first.md) | Evaluation-first | **Adopt** | Acceptance criteria + Test Specs gate Done. |
| [DP-031](../../AgenticBlox/design-principles/DP-031-progressive-autonomy.md) | Progressive Autonomy | **Adopt** | Admin actions (pin/unpin model, set `num_ctx`, set `keep_alive`) phased: read-only first, then user actions, admin write last. |
| [DP-032](../../AgenticBlox/design-principles/DP-032-privacy-tiers.md) | Privacy Tiers | **Adopt** | Per-user chat history is private. Admin sees aggregate metrics, not message contents. |
| [DP-033](../../AgenticBlox/design-principles/DP-033-professional-referral.md) | Professional Referral | Skip | No domain-advice surfaces in the cockpit. |

## Architectural

| AgenticBlox DP | Title | Bucket | Cockpit interpretation |
|---|---|---|---|
| [DP-003](../../AgenticBlox/design-principles/DP-003-agents-right-sized.md) | Agents: Right-sized | Skip | No agents. |
| [DP-004](../../AgenticBlox/design-principles/DP-004-skills-lazy-loaded.md) | Skills: Lazy-loaded | Skip | No agent skills. |
| [DP-005](../../AgenticBlox/design-principles/DP-005-cost-awareness.md) | Cost-awareness | **Adopt** | Token-counting view in the dashboard (`messages.usage_in/out`). Per-call cost ledger is v0.2 (US-CL). |
| [DP-007](../../AgenticBlox/design-principles/DP-007-simplicity-over-elegance.md) | Simplicity over Elegance | **Adopt** | Cockpit is operator surface; readable beats clever. |
| [DP-008](../../AgenticBlox/design-principles/DP-008-framework-agnostic-escape-hatch.md) | Framework-Agnostic / Escape-Hatch | **Adopt** | Backend talks to LLMs through `LLMChat` port — Ollama and vLLM are adapters. |
| [DP-009](../../AgenticBlox/design-principles/DP-009-trigger-pluralism.md) | Trigger-Pluralism | Skip | Cockpit is interactive only; no cron / webhook triggers. |
| [DP-010](../../AgenticBlox/design-principles/DP-010-capability-based-llm-routing.md) | Capability-based LLM Routing | **Adopt** | Chat → `gemma4:26b`, Code → `qwen3-coder:30b`, on-demand `deepseek-r1:32b` / `qwen2.5-72B-AWQ`. |
| [DP-011](../../AgenticBlox/design-principles/DP-011-reality-loop-hardware.md) | Reality-Loop with Hardware | **Adopt** | `app/telemetry.py` samples `nvidia-smi` every 5 s. GPU temp/VRAM/power exposed in the dashboard. |
| [DP-012](../../AgenticBlox/design-principles/DP-012-local-first-cloud-joker.md) | Local-First, Cloud as Joker | **Adopt** | LAN-only by GOALS §non-goals. No cloud LLMs in v0.1. |
| [DP-013](../../AgenticBlox/design-principles/DP-013-memory-write-boundaries.md) | Memory with Clear Write Boundaries | **Adopt (cockpit nuance)** | The chat router is the **only** writer to `messages` and `conversations`. Admin / dashboard / telemetry routes never write those tables. |
| [DP-014](../../AgenticBlox/design-principles/DP-014-governance-budget-contracts.md) | Governance & Budget Contracts | **Adopt** | Admin-only routes for `keep_alive`, model pin, `num_ctx`. Scheduler enforces single-flight; cockpit honours its verdict. |
| [DP-015](../../AgenticBlox/design-principles/DP-015-reserved-placeholder.md) | *Reserved* | n/a | — |
| [DP-025](../../AgenticBlox/design-principles/DP-025-agent-as-last-resort.md) | Agent as Last Resort | Skip | No agents. |
| [DP-026](../../AgenticBlox/design-principles/DP-026-compose-over-monolith.md) | Compose over Monolith | Skip | No agents. |
| [DP-027](../../AgenticBlox/design-principles/DP-027-right-delivery-form.md) | Right Delivery Form | **Adopt (decided once)** | Web service + multi-page UI. Verdict logged in ADR-002; not re-run per spec. |
| [DP-028](../../AgenticBlox/design-principles/DP-028-standard-over-invention.md) | Standard over Invention | **Adopt** | shadcn / Recharts / FastAPI / SQLAlchemy / SSE / bcrypt — stock components. |
| [DP-029](../../AgenticBlox/design-principles/DP-029-hexagonal-architecture.md) | Hexagonal Architecture | **Adopt** | Three ports: `LLMChat` (chat/code), `SchedulerControl`, `Telemetry`. Adapters: `OllamaLLMChat`, `VLLMChat`, `SchedulerHTTP`, `NvidiaSmi`. **Bound by DG-004** — every spec crossing the boundary carries the DG-004 block. |
| [DP-030](../../AgenticBlox/design-principles/DP-030-scalability-via-template.md) | Scalability via Template | Defer | Single-tenant in v0.1. Re-evaluate if multi-tenant lands in v0.2. |
| [DP-034](../../AgenticBlox/design-principles/DP-034-vault-as-agent-memory.md) | Vault as Agent Memory | Skip | No agents. |
| [DP-035](../../AgenticBlox/design-principles/DP-035-research-toolbelt-breadth.md) | Research Toolbelt Breadth | Skip | No agents. |
| [DP-036](../../AgenticBlox/design-principles/DP-036-mcp-inventory-and-policy.md) | MCP Inventory and Policy | Defer | Cockpit becomes a tool host only in v0.2 (US-MCP). |

## Process

All process DPs are adopted unchanged. They govern **how** we build the cockpit, and the cockpit's process mirrors AgenticBlox.

| AgenticBlox DP | Title | Bucket |
|---|---|---|
| [DP-017](../../AgenticBlox/design-principles/DP-017-design-before-implementation.md) | Design before Implementation | **Adopt** |
| [DP-018](../../AgenticBlox/design-principles/DP-018-versioned-contracts-semver.md) | Versioned Contracts (SemVer) | **Adopt** |
| [DP-019](../../AgenticBlox/design-principles/DP-019-testability-reproducibility.md) | Testability & Reproducibility | **Adopt** |
| [DP-020](../../AgenticBlox/design-principles/DP-020-english-as-system-language.md) | English as System Language | **Adopt** |
| [DP-021](../../AgenticBlox/design-principles/DP-021-agile-sprint-methodology.md) | Agile Sprint Methodology | **Adopt** |
| [DP-022](../../AgenticBlox/design-principles/DP-022-documentation-first-class.md) | Documentation as First-Class | **Adopt** |
| [DP-023](../../AgenticBlox/design-principles/DP-023-git-centered-governance.md) | Git-centered Change Governance | **Adopt** |
| [DP-024](../../AgenticBlox/design-principles/DP-024-vault-source-of-truth.md) | Vault as Source of Truth | **Adopt** |

---

## Decision Guides bound to adopted DPs

Of the four DGs in AgenticBlox `decision-guides/`:

| DG | Binds | Applies to cockpit? |
|----|-------|---------------------|
| [DG-001](../../AgenticBlox/decision-guides/DG-001-should-this-be-an-agent.md) | DP-025 | No (no agents). |
| [DG-002](../../AgenticBlox/decision-guides/DG-002-should-this-agent-be-split.md) | DP-026 | No. |
| [DG-003](../../AgenticBlox/decision-guides/DG-003-what-delivery-form.md) | DP-027 | Once — recorded in ADR-002. |
| [DG-004](../../AgenticBlox/decision-guides/DG-004-port-or-adapter.md) | DP-029 | **Yes — binding.** Required block on every Functional Spec that touches the platform boundary (scheduler, Ollama, vLLM, `nvidia-smi`). |

---

## History

- **v1.0 (2026-04-27)** — Initial Accepted version. Inherits AgenticBlox DP-INDEX v1.4 with the Adopt / Defer / Skip mapping above. No cockpit-specific DPs added yet.
