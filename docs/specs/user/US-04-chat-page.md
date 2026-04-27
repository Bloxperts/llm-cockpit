<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# US-04 · User Spec — Chat interface

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-04-chat-page.md`](../functional/US-04-chat-page.md)
**Test Spec:** [`test/US-04-chat-page.md`](../test/US-04-chat-page.md)
**Sprint:** 4
**Depends on:** US-01 (login), US-07 (Ollama integration), ADR-004 (role gate; chat-tagged model picker).
**Min role:** `chat`.

## Story

> As any user with role `chat` or higher I want a Claude-shaped chat interface where I pick a chat-tagged model from whatever Ollama is currently serving and have a normal streaming conversation, with my history persisted across sessions, so that I have a comfortable local-LLM chat surface that doesn't require me to memorise model names or use a CLI.

## Target state

`/chat` shows:

- **Conversation list** (left rail). My past chat conversations, sorted newest first. Click to resume. New-conversation button at top.
- **Model picker** (top of pane). Dropdown listing every model currently tagged `chat` or `both`. The picker remembers my last-used model per conversation. Default for a new conversation is the user's last-used chat model (or the first chat-tagged model if none yet).
- **Conversation pane.** Streaming Claude-shaped messages: my message right-aligned, model's message left-aligned with model name + token-count footer. Code blocks rendered with syntax highlighting and a copy button. Markdown rendered.
- **Composer** (bottom). Text area, Send button, keyboard shortcut `Ctrl/Cmd+Enter` to send.
- **System prompt** (per-conversation, optional). Collapsed accordion at the top of the pane: "System prompt (none) — click to set". When set, it's prepended to every turn; saved with the conversation.

Behaviour:

- Streaming via SSE; first token appears within ~300 ms when the model is warm.
- If Ollama drops mid-stream, the partial reply is saved and the UI shows "Connection lost — retry" beneath the message.
- The picker only shows chat-tagged models; code-tagged models are filtered out.
- All chat traffic goes through the cockpit backend's `LLMChat` port, not directly to Ollama from the browser. (Owned by US-07; mentioned here for completeness.)

## Acceptance criteria

1. A `chat`, `code`, or `admin` user can open `/chat` and pick any chat-tagged model.
2. The picker excludes models tagged `code` (it shows `chat` and `both`).
3. Sending a message streams the model's reply token-by-token; first token within 300 ms when the target model is warm.
4. The conversation appears in the left-rail list and is fully resumable after logout/login.
5. Code blocks render with syntax highlighting and a working copy button.
6. Setting a system prompt and starting a conversation: the system prompt is sent to Ollama on every turn; it is saved with the conversation; resuming the conversation loads the same system prompt.
7. Token counts (`prompt_eval_count`, `eval_count`) are extracted from Ollama's final SSE event and shown beneath the model's message and persisted in `messages.usage_in / usage_out`.
8. A user with role `chat` who somehow navigates to `/code` is redirected to `/chat`. (Defence in depth — backend is the real gate.)

## Scope boundaries (out)

- Image input (vision models) — v0.2.
- Tool-call / MCP rendering — v0.2.
- A/B compare two models on the same prompt — v0.2.
- Conversation export — v0.2.
- Rich attachments (PDF, files) — v0.2.
- Search across conversations — v0.2.

## Notes

- Same backend machinery as US-05. The split is which models the picker shows and which page the user lands on.
- DG-004 inheritance from US-07 (one outbound port: `LLMChat`). Functional spec records the inheritance.
