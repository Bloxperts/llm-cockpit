# Cockpit Retirement

**Date:** 2026-06-03

`Bloxperts/llm-cockpit` is retired as an active product repository.

## Decision

Keep the repository for history, but stop using it as an active deployment source or feature target.

## Replacement Boundary

- `Bloxperts/bloxboard-runtime` owns BloxBoard shell, module UI frames, and BloxBoard-facing API read models.
- `Bloxperts/agentic-blox` owns AgenticBlox platform/runtime/module logic.
- Cockpit historical code remains reference material only.

## Runtime Posture

- Cortex is retired and must not be treated as a routeable/default endpoint.
- Conductor observer defaults are disabled.
- Existing Cockpit services should not be deployed from local dirty worktrees.
- Useful code should be extracted into active repos through normal PR review.

## Final Active Maintenance

The final active maintenance pass removed Cortex as an active BloxGuard probe and marked the repo retired.
