<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# US-05 · User Spec — Code interface

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-05-code-page.md`](../functional/US-05-code-page.md)
**Test Spec:** [`test/US-05-code-page.md`](../test/US-05-code-page.md)
**Sprint:** 4
**Depends on:** US-01 (login), US-04 (shares the backend chat machinery), US-07 (Ollama integration), ADR-004 (role gate; code-tagged model picker).
**Min role:** `code`.

## Story

> As any user with role `code` or higher I want a Claude-Code-shaped interface where I pick a code-tagged model from whatever Ollama is currently serving and have a code-emphasised conversation, with my code history persisted separately from my chat history, so that I can have a focused coding surface without switching tools.

## Target state

`/code` mirrors `/chat` (left rail of conversations, model picker, conversation pane, composer, system prompt) with three differences:

1. **Model picker** lists only models tagged `code` or `both` (heuristic + admin override per ADR-004 §3).
2. **Default system prompt** is a short coder system prompt — pre-filled on new conversations, editable. The default prompt lives in `config/code_default_system_prompt.md` and an admin can edit it on the Ollama config page (US-10). The user can override per conversation.
3. **Code-emphasis rendering**: monospace input area, larger code-block fonts, side-by-side "diff view" for replies that contain `+++/---` markers, "wrap long lines" toggle in the conversation pane.

Conversations on `/code` and `/chat` are separate lists; they don't intermix in the left rails. Internally they share the same `conversations` and `messages` tables but carry a `mode` column (`chat` / `code`).

## Acceptance criteria

1. A `code` or `admin` user can open `/code` and pick any code-tagged model. A user with role `chat` cannot open `/code` (backend returns 403; UI hides the link).
2. The picker excludes models tagged `chat` only (it shows `code` and `both`).
3. Sending a message streams the model's reply; first token within 300 ms when the target model is warm.
4. The conversation appears in the `/code` left-rail list and is fully resumable. It does not appear in the `/chat` list.
5. Code-block fonts and "wrap long lines" toggle work as designed on a sample reply with both short and long lines.
6. The default code system prompt is pre-filled on new conversations and can be cleared or replaced by the user.
7. Token-count footers and `messages.usage_in / usage_out` work identically to US-04.
8. Diff view renders correctly when the reply contains a section delimited by `--- a/file.py` / `+++ b/file.py` markers.

## Scope boundaries (out)

- Real file system integration / "apply this diff to my repo" — v0.2 at earliest, possibly out of project scope entirely.
- Multi-file projects, repository indexing — out of scope.
- Sandbox execution / "run this code and show output" — out of scope.
- Inline lint / type-check feedback — out of scope.
- IDE-style autocomplete on the composer — out of scope.

## Notes

- Same backend machinery as US-04; the split is the picker filter, default system prompt, and rendering tweaks.
- DG-004 inheritance from US-07 (one outbound port: `LLMChat`). No new boundary surface.
- "Diff view" is a rendering nicety, not a contract — if a model's output doesn't include diff markers, the message renders as a normal code block.
