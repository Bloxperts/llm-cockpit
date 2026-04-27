<!-- Status: Draft | Version: 0.1 | Created: 2026-04-26 -->
# Use Cases — LLM Cockpit v0.1

| ID | Title | Page | Spec | Status |
|---|---|---|---|---|
| US-01 | Family member logs in to the cockpit | login | [SPEC-001](../specs/SPEC-001-login.md) | Draft |
| US-02 | Operator sees live model + GPU + queue state | dashboard | [SPEC-002](../specs/SPEC-002-dashboard-live.md) | Draft |
| US-03 | Operator sees historical metrics (24 h, 7 d) | dashboard | [SPEC-003](../specs/SPEC-003-dashboard-history.md) | Draft |
| US-04 | User chats with the local orchestrator (gemma4:26b) | chat | [SPEC-004](../specs/SPEC-004-chat-page.md) | Draft |
| US-05 | User uses the coder model (qwen3-coder:30b) for code | code | [SPEC-005](../specs/SPEC-005-code-page.md) | Draft |
| US-06 | Admin pins/unpins models and adjusts num_ctx live | admin | [SPEC-006](../specs/SPEC-006-admin-controls.md) | Draft |
| US-07 | All chat / code calls go through the scheduler queue | infrastructure | [SPEC-007](../specs/SPEC-007-scheduler-routing.md) | Draft |

## Future / v0.2+

| ID | Title | Notes |
|---|---|---|
| US-V1 | Provide the data needed for agentic-blox US-V01 (32 k validation) | Per-user p95 of prompt-token sizes over 7 days |
| US-A1 | A/B compare two models side-by-side on same prompt | Useful for short-list refinement |
| US-CL | Per-call cost ledger | When tokens-to-€ pricing exists |
| US-EX | Export conversation to vault as markdown | Cockpit-to-vault loop |
| US-MCP | Inline MCP tool-call display | Cockpit becomes a tool host |
