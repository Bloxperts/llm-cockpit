<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-30 | Updated: 2026-04-30 -->
# UC-12 - Functional Spec - UI refresh and interaction polish

**Status:** Accepted
**User Spec:** [`../../use-cases/UC-12-ui-refresh.md`](../../use-cases/UC-12-ui-refresh.md)
**Test Spec:** [`../test/UC-12-ui-refresh.md`](../test/UC-12-ui-refresh.md)
**Depends on:** UC-01..UC-10, UC-02 v1.1, ADR-004.
**Bound DG:** none. This is frontend/product interaction work over existing backend APIs.

## Goal

Bring the cockpit to a release-quality UI before the `1.0.0` PyPI sprint. The result should be smooth, functional, and aesthetic without turning the app into a marketing site. The primary interface remains an operational tool for local Ollama usage.

## Design direction

Sprint 11 adopts the **Hybrid: Admin Dashboard + Premium Chat** direction:

- Dashboard and placement board are dense, operational, and fast to scan.
- Chat/code are calm, readable workspaces.
- Admin pages are quiet settings surfaces with predictable controls.
- Visual effects are restrained: transitions, focus rings, hover/drop states, drawer movement, and streaming polish.

Avoid:

- landing-page heroes;
- decorative gradient/orb backgrounds;
- oversized card-heavy marketing composition;
- one-note palettes;
- hidden instructions replacing real controls.

## Surfaces in scope

1. App shell: header/sidebar/navigation/account menu.
2. Dashboard live view:
   - GPU cards;
   - model placement columns;
   - model cards;
   - perf-test action and drawer;
   - pull/delete/place controls;
   - empty/unreachable/loading states.
3. Dashboard history tab visual alignment.
4. Chat page.
5. Code page.
6. Admin users page.
7. Admin Ollama page.
8. Login and forced password change screens.

## Placement-board interaction

The placement board should support drag-and-drop for model placement when practical.

Requirements:

- use a proven React DnD library already compatible with the stack, unless native HTML drag/drop is clearly simpler and accessible;
- card drag starts only from a clear handle or the card body without interfering with buttons/menus;
- columns provide visual drop affordance;
- after drop, call the existing placement API;
- while the request is pending, show a stable pending state;
- on failure, restore the previous visible placement and show an error;
- preserve an explicit select/menu fallback for keyboard, touch, and accessibility;
- keep requested-vs-actual and mismatch signals visible when available.

## Design system work

Add or normalize reusable frontend pieces where useful:

- app shell layout primitives;
- button/icon button variants;
- badge/status pill variants;
- drawer/dialog styling;
- card/list/table styles;
- form controls/select/menu styles;
- loading skeletons;
- empty/error banners;
- tooltip pattern for icon-only controls.

Use existing stack conventions and local components. Add dependencies only if they materially reduce complexity.

## Accessibility and responsiveness

- Keyboard users can reach all primary actions.
- Focus states are visible.
- Icon-only actions have accessible labels/tooltips.
- Text does not overlap or overflow buttons/cards at mobile and desktop widths.
- Drag-and-drop has a non-drag fallback.
- Color is not the only signal for warning/error/success.

## Non-functional requirements

- No backend authorization changes.
- No schema migration expected.
- No change to role capabilities.
- Keep the UI performant enough for live SSE/chat/dashboard updates.
- Existing tests remain green.

## Acceptance criteria

1. Dashboard, chat, code, admin users, admin Ollama, login, and change-password share the same visual system.
2. Placement board supports smooth drag-and-drop plus accessible fallback.
3. Perf-test drawer remains fully functional and visually integrated.
4. Chat/code streaming and code block actions still work.
5. All role-gated controls remain correctly hidden/visible on the frontend and enforced by existing backend tests.
6. Desktop and mobile browser smoke screenshots show no overlapping content.
7. Frontend build passes.
8. Existing backend tests pass.
9. New frontend/unit/component or browser-smoke tests cover the highest-risk UI state changes.

## Open questions for Chris before acceptance

1. Preferred tone: mostly **Operational Cockpit**, mostly **Premium Workspace**, or the proposed **Hybrid**?
2. Should Sprint 11 include only dashboard + shell first, or all major surfaces in one sprint?
3. Is adding a DnD dependency acceptable if it keeps the placement board robust?
