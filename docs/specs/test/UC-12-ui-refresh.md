<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-30 | Updated: 2026-04-30 -->
# UC-12 - Test Spec - UI refresh and interaction polish

**Status:** Accepted
**User Spec:** [`../../use-cases/UC-12-ui-refresh.md`](../../use-cases/UC-12-ui-refresh.md)
**Functional Spec:** [`../functional/UC-12-ui-refresh.md`](../functional/UC-12-ui-refresh.md)

## Approach

The UI refresh is validated through a mix of existing backend tests, frontend build/tests, focused component tests for stateful interactions, and browser smoke screenshots at desktop/mobile sizes. The goal is not pixel-perfect snapshot testing; it is catching broken flows, unreadable states, and regressions in accepted behavior.

## Automated test cases

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1 | Frontend builds successfully after the redesign. | `npm run build` |
| T-02 | AC-2 | Placement board drag/drop calls the existing place endpoint with the expected placement and shows pending/error behavior. | component test or browser test with mocked API |
| T-03 | AC-2 | Placement board fallback select/menu still works without drag. | component test |
| T-04 | AC-3 | Perf-test drawer still renders progress, heartbeat/stalled, result, cancelled, and error states. | component test |
| T-05 | AC-4 | Chat/code code-block copy/download and streaming layout still render correctly. | component test or smoke |
| T-06 | AC-5 | Role-gated navigation/action visibility matches role. | component test |
| T-07 | AC-6 | Desktop dashboard screenshot has non-empty GPU/model board content and no obvious overlap. | browser smoke |
| T-08 | AC-6 | Mobile dashboard/chat screenshots have no obvious overlap or horizontal overflow. | browser smoke |
| T-09 | AC-8 | Existing backend suite stays green. | `pytest` |

## Manual smoke

| ID | Description | Expected |
|----|-------------|----------|
| M-01 | On Neuroforge or local dev, log in as admin and move a model between placement columns. | Drag/drop feels smooth; fallback control still works; failed placement is recoverable. |
| M-02 | Run perf-test drawer through progress, cancel, and result. | UI remains clear; cancel visible until terminal event; stalled warning appears if events pause. |
| M-03 | Chat with a chat-tagged model and open code mode. | Streaming and code blocks remain readable and responsive. |
| M-04 | Resize browser from desktop to mobile widths across dashboard/admin/chat. | No broken layout or overlapping text. |

## Pass criteria

- Automated tests/builds pass.
- Browser smoke screenshots are reviewed before sprint acceptance.
- Manual smoke has no blocking UX issue.
- Any deferred UI issue is recorded in `SPRINT_STATE.md` or a follow-up backlog note.
