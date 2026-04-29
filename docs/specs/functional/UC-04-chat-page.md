<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# UC-04 · Functional Spec — Chat interface

**Status:** Accepted
**Depends on:** UC-01 (login), UC-07 (Ollama integration via `LLMChat` port), ADR-004 (role gate; chat-tagged picker).
**Min role:** `chat`.
**User Spec:** [`../../use-cases/UC-04-chat-page.md`](../../use-cases/UC-04-chat-page.md)
**Test Spec:** [`../test/UC-04-chat-page.md`](../test/UC-04-chat-page.md)
**Bound DG:** DG-004 — chat traffic crosses the boundary via the `LLMChat` port covered by UC-07's DG-004 block. This spec inherits that block; if a *new* boundary surface is added (e.g. a second LLM provider direct from the chat router), DG-004 must be re-run here.

## Goal

Claude-shaped chat surface for `chat` / `code` / `admin` users to converse with any chat-tagged model that Ollama is currently serving. Streaming responses, code-block syntax highlighting, per-user history, per-conversation system prompt, model-picker filtered to chat-tagged models.

## UX

- Three-column layout: conversation list (left, ~280 px), chat pane (centre, flex), info rail (right, collapsible — shows the picked model, num_ctx used so far, total tokens this conversation).
- Each conversation has: auto-generated title (from first user turn), the model it was started with, created/updated timestamps, optional system prompt.
- New-conversation button at the top of the list.
- Message bubbles: user right-aligned, assistant left-aligned. Markdown (GFM) rendered. Code blocks have language label + copy button.
- Composer: textarea, Enter sends, Shift+Enter newline, `Ctrl/Cmd+K` opens model-picker overlay.
- Stop-streaming button replaces "Send" while a response streams.
- "Regenerate" on the last assistant message — re-runs with the same prompt + system prompt.
- Scroll-to-bottom auto-anchored when streaming; releases when user scrolls up.

## Model picker

- Source: `LLMChat.list_models()` (UC-07) joined with `model_tags` (ADR-004 §3).
- Filter: shows models whose tag is `chat` or `both`.
- Sort: most-recently-used by this user first; otherwise alphabetical.
- The picker is **not** asked to start an `ollama pull` if the model isn't installed — installation is UC-10 (admin only).
- Switching models *within* an existing conversation is allowed; the info rail warns "the new model sees only the visible transcript, not the previous model's hidden reasoning". The new model name is recorded on subsequent messages, but the conversation row's `model` column stays as the original to preserve history.

## Streaming protocol

```
POST /api/chat/{conversation_id}/stream    body { content }
                                           → SSE
                                                 event: token  data: "..."
                                                 event: usage  data: {prompt_tok, completion_tok, gen_tps}
                                                 event: done   data: {message_id}
                                                 event: error  data: {code, message}
```

The backend uses `LLMChat.chat_stream(model, messages, options)` from UC-07. It re-emits Ollama's NDJSON chunks as SSE `token` events, then a final `usage` and `done`. On error the SSE channel emits `error` and closes; the backend persists whatever was received with `usage_*` set to the partial extracts.

If the user stops the stream from the UI: the backend stops *reading* from the upstream port (Ollama itself doesn't support cancel — DP-028 reality). The partial reply is saved.

## Persistence

- Tables: `conversations`, `messages`. New column on `conversations`: `mode TEXT NOT NULL DEFAULT 'chat'` (one of `chat`, `code`) so UC-05 reuses the same schema.
- New column on `conversations`: `system_prompt TEXT NULL` (per-conversation; nullable; saved when the user sets it).
- Each `messages` row: `id, conversation_id, role ('user'|'assistant'|'system'), content, model, usage_in, usage_out, latency_ms, gen_tps, ts, error TEXT NULL`.
- On reload, messages render from DB.
- No edit-message-and-replay in v0.1. Only regenerate-last-assistant.
- Conversation rename: click title → inline edit.
- Delete: button in the info rail, confirms with "Delete this conversation? This cannot be undone." Hard delete in v0.1 (admin-side soft-delete is for users, not their conversations).

## API

```
POST /api/chat                              → 201 { conversation_id, mode: "chat" }
GET  /api/chat                              → list of own chat conversations: {id, title, model, created_at, updated_at, message_count}
GET  /api/chat/{id}                         → full conversation + messages
POST /api/chat/{id}/stream                  → SSE  (uses LLMChat.chat_stream)
POST /api/chat/{id}/regenerate              → SSE  (re-runs last user turn with current system prompt + model)
PATCH /api/chat/{id}                        → { title?, model?, system_prompt? }
DELETE /api/chat/{id}                       → 204
GET  /api/models?tag=chat                   → list of pickable chat / both models (via LLMChat.list_models)
```

All routes are gated by `Depends(require_role("chat"))` per ADR-004 §4.

## Acceptance criteria

- ✅ A `chat` / `code` / `admin` user can open `/chat` and pick any chat-tagged model.
- ✅ The picker excludes models tagged `code` only.
- ✅ First token visible within 300 ms of pressing Enter (warm model, &lt; 32 k prompt).
- ✅ Streaming smoother than 50 ms between tokens.
- ✅ Stopping mid-stream actually stops re-emitting tokens to the browser; partial reply is saved with `error="stream_aborted"`.
- ✅ Markdown / code blocks render correctly for at least: Python, TypeScript, Bash, SQL, JSON.
- ✅ Conversation persists across browser refresh and across logout/login.
- ✅ Two users in different browsers see only their own conversations.
- ✅ Setting a per-conversation system prompt: the prompt is sent to Ollama on every turn; saved with the conversation; loaded on resume.
- ✅ Token counts are captured from Ollama's final NDJSON event into `usage_in / usage_out`.

## Notes

- This spec **does not** define Ollama-side `keep_alive` or `num_ctx` overrides — both default to whatever Ollama itself returns. Pushing per-call overrides is v0.2 ("Model Lifecycle").
- Defence-in-depth: `chat` users navigating to `/code` get a 403 from `/api/code/*`; the frontend redirects them to `/chat`. The actual gate is the backend.
