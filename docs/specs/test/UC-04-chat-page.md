<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-04-chat-page · Test Spec — Chat page

**Status:** Accepted
**Owner:** Chris
**User Spec:** [`user/UC-04-chat-page.md`](../../use-cases/UC-04-chat-page.md)
**Functional Spec:** [`functional/UC-04-chat-page.md`](../functional/UC-04-chat-page.md)

<!-- VAULT-SYNC: body filled in on develop in feature/UC-04-UC-05-chat-code as
the first commit of Sprint 4 (per the runbook). Status flipped Draft → Accepted;
version stays 0.1. Mirror in vault and re-sync /docs at sprint review. -->

## Approach

Pure pytest + httpx `TestClient` with `FakeLLMChat` injected. No real Ollama,
no real GPU. Each test owns a fresh in-memory SQLite DB via `tmp_path`.
Streaming is exercised via `client.stream("POST", ...)` plus byte-level
parsing of the SSE event stream — same pattern UC-02 used for the perf
harness (`test_perf_test_emits_stage_sequence_*`). Per-user isolation is
exercised by issuing two TestClient logins from the same in-memory DB.

The Ollama NDJSON wire-shape pinning that was deferred from UC-07 lands as
part of this slice in `tests/test_uc07_port.py` — see the parametrised
chat_stream tests.

## Test cases

Reference: UC-04 functional spec + ACs.

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1 | A `chat`/`code`/`admin` user can `POST /api/chat` and gets `201 {conversation_id, mode: "chat"}`. | auto |
| T-02 | AC-1, AC-7 | `GET /api/chat` returns the user's own conversations sorted by `updated_at` desc. | auto |
| T-03 | AC-7 | `GET /api/chat/{id}` returns the conversation + ordered messages. | auto |
| T-04 | (role gate) | `chat` role gets 403 on every `/api/code/*` route (cross-checked in UC-05 test spec). | auto |
| T-05 | AC-3, AC-4 | `POST /api/chat/{id}/stream` returns SSE: token events while streaming, one `usage` event with `prompt_tok`/`completion_tok`/`gen_tps`, one `done` event with `message_id`. | auto |
| T-06 | AC-9 | The final `messages` row carries `usage_in`, `usage_out`, `gen_tps`, and `latency_ms` populated from the final NDJSON chunk. | auto |
| T-07 | AC-5 | If the chat stream raises mid-emission (network drop / `OllamaStreamAbortedError`), the partial assistant message is persisted with `error="stream_aborted"` and what was streamed before the cut. | auto |
| T-08 | AC-5 | If the upstream returns `OllamaModelNotFound` mid-stream, the SSE channel emits an `error` event and the client connection closes cleanly. | auto |
| T-09 | AC-1, AC-2 | `GET /api/models?tag=chat` returns the union of `model_tags.tag IN ('chat','both')`; excludes models tagged `code` only. | auto |
| T-10 | AC-7 | `PATCH /api/chat/{id}` with `{title}` updates the title and bumps `updated_at`. | auto |
| T-11 | AC-7 | `PATCH /api/chat/{id}` with `{system_prompt}` persists the prompt; subsequent `/stream` calls include it as a `system` message ahead of the user content. | auto |
| T-12 | AC-7 | `DELETE /api/chat/{id}` returns 204 and the conversation + messages are gone. | auto |
| T-13 | AC-8 | User A cannot `GET / PATCH / DELETE` user B's conversation — 404 (we return 404 not 403 to avoid leaking conversation existence). | auto |
| T-14 | (regenerate) | `POST /api/chat/{id}/regenerate` after at least one user→assistant turn re-runs the last user prompt with the same model + system_prompt; emits new SSE events; appends a new assistant message (the regenerated row replaces nothing — the old one is kept). | auto |
| T-15 | AC-3 | "First token visible within 300 ms" is asserted in the cockpit's path: from `POST` request to first `event: token` SSE byte under 100 ms wall-clock with `FakeLLMChat`. The full Ollama-side timing is integration-test territory. | auto |
| T-16 | (model switch) | Setting `model` via `PATCH` while a conversation has prior messages is allowed and persists; the `conversations.model` column is **not** updated (the original model is the conversation's identity). | auto |
| T-17 | (auth) | All `/api/chat/*` routes require `Depends(require_role("chat"))` — unauthenticated → 401, settled gate → 409 if `must_change_password`. | auto |

## Pass criteria

- All cases T-01..T-17 pass on `develop` and on `main`.
- `pytest --cov` ≥ 90 % on `cockpit/routers/chat.py` and `cockpit/services/chat.py`.
- The full prior 229-test suite stays green; the deferred chat_stream NDJSON skip in `tests/test_uc07_port.py` is **resolved** (replaced with parametrised wire-shape contract tests as part of this slice).
- Manual smoke at sprint review: log in as `admin`, send a chat to a chat-tagged model, watch tokens stream in real time, refresh the page, the conversation reloads, click Regenerate, see a new assistant turn.

## Out of scope this slice

- Edit-message-and-replay (per UC-04 §Persistence — explicit non-goal).
- Conversation export / share — v0.2.
- Per-call `keep_alive` / `num_ctx` overrides per the spec note (v0.2 "Model Lifecycle"; placement governs warm-state, not chat options).
- Frontend Vitest tests — the Sprint-4 frontend gets a manual smoke at review.

## Tools

- pytest, pytest-cov, httpx (already in `[dev]`).
- `FakeLLMChat` from UC-07 + `calls` accumulator added in UC-02.
- `EventSourceResponse` from `sse-starlette`.
