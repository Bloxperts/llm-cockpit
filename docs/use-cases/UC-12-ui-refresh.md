<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-30 | Updated: 2026-04-30 -->
# UC-12 - Use Case - UI refresh and interaction polish

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`functional/UC-12-ui-refresh.md`](../specs/functional/UC-12-ui-refresh.md)
**Test Spec:** [`test/UC-12-ui-refresh.md`](../specs/test/UC-12-ui-refresh.md)
**Sprint:** 11
**Depends on:** UC-01..UC-10 Done/User Accepted, UC-02 v1.1, LL-001.
**Min role:** all logged-in roles, with admin-only controls still gated by role.

## Story

> As a cockpit user, I want the app to feel smooth, coherent, and pleasant while staying functional, so that daily model work feels like a polished product rather than a collection of admin pages.

> As an admin, I want drag-and-drop and responsive controls where they naturally fit, especially on the model placement board, so that common operations feel direct and trustworthy.

## Target state

The cockpit keeps its current functional scope, but the interface is redesigned around a consistent product system:

- a calm app shell with clear navigation and role-aware actions;
- a dashboard that reads as an operational cockpit, not a marketing page;
- a model placement board with drag-and-drop where practical, plus accessible fallback controls;
- polished chat and code workspaces with smooth streaming, readable messages, and restrained controls;
- admin pages that feel like dense settings tools rather than separate prototypes;
- consistent drawers, dialogs, buttons, badges, tables, tabs, empty states, loading states, error states, and focus states.

## Design direction

Preferred direction for Sprint 11: **Hybrid: Admin Dashboard + Premium Chat**.

- Dashboard: operational clarity, dense but scannable model/GPU state, strong placement affordances.
- Chat/code: calmer workspace feel, premium typography, clean message surfaces.
- Admin: restrained configuration panels built for repeated use.

## Acceptance criteria

1. The app has one coherent design language across dashboard, chat, code, admin users, admin Ollama, login, and change-password.
2. Model placement supports drag-and-drop where pointer devices are available and keeps a non-drag fallback for accessibility/mobile.
3. Drag targets provide clear hover/active/drop feedback and never hide requested-vs-actual placement information.
4. The perf-test drawer from UC-02 v1.1 is visually integrated with the new design and keeps stalled/cancel/result states clear.
5. Chat/code streaming remains smooth; message layout, code blocks, copy/download affordances, and scroll behavior remain usable.
6. Loading, empty, error, disabled, focus, and permission-denied states are designed for the touched surfaces.
7. The UI works at common desktop and mobile widths without overlapping text or controls.
8. Role boundaries remain unchanged: hidden frontend actions do not replace backend authorization.
9. No backend behavior changes are introduced unless required to support an already-accepted use case.
10. Playwright or equivalent browser smoke captures dashboard and chat/code views at desktop and mobile sizes before user acceptance.

## Scope boundaries

Out:

- New product features.
- GPU hard-isolation architecture.
- Public PyPI publishing (Sprint 12).
- Native mobile/PWA work.
- New auth providers.
- Marketing landing page.

## Notes

- This is intentionally before PyPI/`1.0.0`; the public release should ship with the final v1 UI, not with a transitional interface.
- If the UI work exposes small install/docs polish needed before PyPI, capture it in UC-11/Sprint 12 rather than expanding this story.
