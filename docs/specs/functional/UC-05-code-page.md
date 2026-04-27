<!-- Status: Done (technical) | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-28 -->
# UC-05 · Functional Spec — Code interface

**Status:** Done (technical)
<!-- VAULT-SYNC: backend implementation landed on develop in feature/UC-04-UC-05-chat-code (Sprint 4). Status flipped Accepted → In Progress → Done (technical). Reuses UC-04's stream_reply() service end-to-end; differences are confined to mode='code', the role gate, and the default system prompt. Mirror in vault and re-sync /docs at sprint review. User Acceptance pending Chris's sprint-review sign-off. -->

**Depends on:** UC-04 (shares backend machinery), UC-07 (Ollama integration via `LLMChat` port), ADR-004 (role gate; code-tagged picker).
**Min role:** `code`.
**User Spec:** [`../../use-cases/UC-05-code-page.md`](../../use-cases/UC-05-code-page.md)
**Test Spec:** [`../test/UC-05-code-page.md`](../test/UC-05-code-page.md)
**Bound DG:** DG-004 — inherits the `LLMChat` port from UC-07. No new boundary surface introduced.

## Goal

Same shell as the chat page, tuned for code workflows. Picker lists code-tagged models; default system prompt is a coder prompt; rendering biased toward larger code blocks and diff visualisation.

## Differences from UC-04

- **Picker filter:** shows models tagged `code` or `both` (ADR-004 §3). Hides chat-only models.
- **Role gate:** `Depends(require_role("code"))` on every `/api/code/*` route. `chat` users get 403; the `/code` link is hidden in their sidebar.
- **Default system prompt:** read from the `settings` row whose key is `code_default_system_prompt` (admin-editable in UC-10). Falls back to `"You are an expert pair programmer. Be terse, produce correct code, and prefer working examples over explanations."` if the row is absent.
- **Composer rendering:** monospace input area; "wrap long lines" toggle in the conversation pane.
- **Inline diff view:** if the assistant's reply contains a section delimited by `--- a/...` / `+++ b/...` markers, render side-by-side diff (using `react-diff-viewer`). Otherwise render as a normal code block.
- **Copy-as-file button** on each code block (saves with the filename hinted by a `# filename: foo.py` first line, when present).

## Storage

Same `conversations` and `messages` tables as UC-04. The `mode` column is set to `code` for conversations created from `/code`. The model picker stores last-used per `(user_id, mode)`.

## API

```
POST /api/code                              → 201 { conversation_id, mode: "code" }
GET  /api/code                              → list of own code conversations
GET  /api/code/{id}                         → full conversation + messages
POST /api/code/{id}/stream                  → SSE  (uses LLMChat.chat_stream)
POST /api/code/{id}/regenerate              → SSE
PATCH /api/code/{id}                        → { title?, model?, system_prompt? }
DELETE /api/code/{id}                       → 204
GET  /api/models?tag=code                   → list of pickable code / both models
```

`/api/code/*` is **not** an alias of `/api/chat/*` in v0.1 — they differ in the role gate and in the default system prompt. They share the same handler functions internally with a `mode` argument.

## Acceptance criteria

- ✅ A `code` or `admin` user can open `/code` and pick any code-tagged model. A `chat` user gets 403 from `/api/code/*` and the `/code` link is hidden in their sidebar.
- ✅ The picker excludes models tagged `chat` only.
- ✅ Default system prompt is pre-filled on new conversations and equals the value of the `code_default_system_prompt` setting, or the hardcoded fallback when no setting row exists.
- ✅ Diff view renders when the reply contains the `--- a/file` / `+++ b/file` markers.
- ✅ Streaming smooth even for 1 000-token outputs.
- ✅ Switching models mid-conversation behaves the same as UC-04.
- ✅ A code conversation does **not** appear in the `/chat` left rail and vice versa.

## Notes

- Hard-coded model names (`qwen3-coder:30b`, `gemma4:26b`) are **removed** in v0.2. The picker is the source of truth.
- `num_ctx` overrides per call are **out of scope** for v0.1 (v0.2 "Model Lifecycle"). Ollama defaults are accepted.
