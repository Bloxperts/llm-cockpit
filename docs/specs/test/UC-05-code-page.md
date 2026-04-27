<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-05-code-page · Test Spec — Code page

**Status:** Accepted
**Owner:** Chris
**User Spec:** [`user/UC-05-code-page.md`](../../use-cases/UC-05-code-page.md)
**Functional Spec:** [`functional/UC-05-code-page.md`](../functional/UC-05-code-page.md)

<!-- VAULT-SYNC: body filled in on develop in feature/UC-04-UC-05-chat-code as
the first commit of Sprint 4 (per the runbook). Status flipped Draft → Accepted;
version stays 0.1. Mirror in vault and re-sync /docs at sprint review. -->

## Approach

Same approach as UC-04 (pytest + TestClient + FakeLLMChat + in-memory
SQLite). UC-05 reuses the chat machinery and adds the role-gate +
default-system-prompt deltas. Most of the test surface is implicit
inheritance from UC-04 — the `code` test file focuses on the differences.

## Test cases

Reference: UC-05 functional spec + ACs.

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1 | A `code`/`admin` user can `POST /api/code` and gets `201 {conversation_id, mode: "code"}`. | auto |
| T-02 | AC-1 | `GET /api/code/{id}` returns the conversation with `mode == "code"`. | auto |
| T-03 | (role gate) | A `chat` user gets 403 on every `/api/code/*` route. (Mirror in `/api/chat/*` is in UC-04.) | auto |
| T-04 | AC-7 | A `code` conversation does **not** appear in `GET /api/chat`; a `chat` conversation does **not** appear in `GET /api/code`. | auto |
| T-05 | AC-3 | New code conversation has `system_prompt` populated from the `settings` row whose key is `code_default_system_prompt`. | auto |
| T-06 | AC-3 | When that settings row is absent, the system prompt falls back to the bundled default in `default_config/code_default_system_prompt.md`. | auto |
| T-07 | AC-2 | `GET /api/models?tag=code` returns the union of `model_tags.tag IN ('code','both')`; excludes models tagged `chat` only. | auto |
| T-08 | AC-5 | `POST /api/code/{id}/stream` streams SSE token events the same way as `/api/chat/{id}/stream` — happy path. | auto |
| T-09 | (regenerate) | `POST /api/code/{id}/regenerate` emits SSE and persists the new assistant message. | auto |
| T-10 | (auth) | All `/api/code/*` routes require `Depends(require_role("code"))` — unauthenticated → 401; settled gate → 409 if `must_change_password`. | auto |

## Pass criteria

- All cases T-01..T-10 pass on `develop` and on `main`.
- `pytest --cov` ≥ 90 % on `cockpit/routers/code.py`. (`services/chat.py` is the shared backend; covered in UC-04's pass criteria.)
- The full prior test suite stays green.
- Manual smoke at sprint review: as `chris` (admin), open `/code`, see the system prompt pre-filled, ask a code-tagged model to write a small Python function, verify the diff-view path renders for an `--- a/` / `+++ b/` reply.

## Out of scope this slice

- Inline diff view rendering — frontend concern; covered by manual smoke. The marker detection is a pure-string check; no backend tests.
- Per-call `num_ctx` overrides (v0.2 "Model Lifecycle").
- Hard-coded model name removal — already done in UC-07 (the picker is the source of truth).

## Tools

Same as UC-04.
