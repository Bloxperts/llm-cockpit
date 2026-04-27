<!-- Status: Draft | Version: 0.1 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-04 · Functional Spec — Chat page

**Status:** Draft
**Depends on:** US-01 (login), US-07 (scheduler routing).
**User Spec:** [`../user/US-04-chat-page.md`](../user/US-04-chat-page.md)
**Test Spec:** [`../test/US-04-chat-page.md`](../test/US-04-chat-page.md)
**Bound DG:** DG-004 — chat traffic crosses the boundary via the `LLMChat` port covered by US-07's DG-004 block. This spec inherits that block; if a *new* boundary surface is added (e.g. a second LLM provider direct from the chat router), DG-004 must be re-run here.

## Goal

Claude-shaped chat surface — sidebar of past conversations, main pane with messages + input, streaming responses, code-block syntax highlighting, copy buttons.

## UX

- Three-column layout (default): conversation list (left, \~280 px), chat (centre, flex), info pane (right, collapsible — shows model, num_ctx used, total tokens this conversation).
- Each conversation has: title (auto-generated from first user turn), model used, created date.
- New conversation button at the top of the list.
- Message bubbles: user right-aligned, assistant left-aligned. Markdown rendered (GFM). Code blocks have language label + copy button.
- Input area: textarea, Enter sends, Shift+Enter newline, Ctrl/Cmd+K opens model-picker.
- Stop-streaming button replaces "Send" while a response streams.
- "Regenerate" button on the last assistant message.
- Scroll-to-bottom auto-anchored when streaming, releases when user scrolls up.

## Models available

In v0.1, the chat page exposes **gemma4:26b** (default) and **deepseek-r1:32b** (heavy reasoning, single-flight, marked with a clock icon). Switching models within an existing conversation is allowed but warns the user that the new model sees only the conversation transcript, not the previous model's hidden reasoning.

## Streaming

```
POST /api/chat/{conversation_id}/stream    body { content }
                                           → SSE
                                                 event: token  data: "..."
                                                 event: usage  data: {prompt_tok, completion_tok, gen_tps}
                                                 event: done   data: {message_id}
                                                 event: error  data: {code, message}
```

The backend wraps `scheduler:8001/v1/generate` with `stream:true`.

## Persistence

- Each conversation has many messages. Persisted in SQLite.
- On reload, messages render from DB.
- No edit-message-and-replay in v0.1. Only regenerate-last-assistant.
- Conversation rename: click title → inline edit.
- Delete: button in the info pane, confirms with "Delete this conversation? This cannot be undone."

## API

```
POST /api/chat                              → 201 { conversation_id }
GET  /api/chat                              → list of {id, title, model, created_at, message_count}
GET  /api/chat/{id}                         → conversation + all messages
POST /api/chat/{id}/stream                  → SSE
POST /api/chat/{id}/regenerate              → regenerate last assistant turn (SSE)
PATCH /api/chat/{id}                        → { title }
DELETE /api/chat/{id}                       → 204
```

## Acceptance criteria

- ✅ First token visible within 300 ms of pressing Enter (warm model, &lt; 32 k prompt).
- ✅ Streaming smoother than 50 ms between tokens.
- ✅ Stopping mid-stream actually cancels the upstream call (backend issues `keep_alive=0` style cancel — Ollama doesn't support cancel; backend stops reading SSE and lets Ollama finish in the background).
- ✅ Markdown / code blocks render correctly for at least: Python, TypeScript, Bash, SQL.
- ✅ Conversation persists across browser refresh.
- ✅ Two users in different browsers see only their own conversations.
- ✅ When `deepseek-r1:32b` is selected and another user already has a heavy call in flight, the UI shows "Waiting for heavy slot" with elapsed time, eventually completes.
