<!-- Status: Draft | Version: 0.1 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-05 · Functional Spec — Code page

**Status:** Draft
**Depends on:** US-04 (chat page — same machinery).
**User Spec:** [`../user/US-05-code-page.md`](../user/US-05-code-page.md)
**Test Spec:** [`../test/US-05-code-page.md`](../test/US-05-code-page.md)
**Bound DG:** DG-004 — same as US-04 (inherits the `LLMChat` port from US-07). No new boundary surface introduced.

## Goal

Same shell as the chat page, but tuned for code workflows. Default model = `qwen3-coder:30b`. UI affordances biased toward longer outputs and structured code.

## Differences from chat page

- **Default model:** `qwen3-coder:30b`. Switching to `gemma4:26b` allowed but warned.
- **Default** `num_ctx`**:** 32 768 (matching the deployment cap).
- **System prompt picker:** dropdown with 3 presets — "Refactor", "Explain", "Generate from spec". User can edit the active system prompt inline (per-conversation).
- **Code editor for input:** the input box uses a Monaco-like component with language detection and syntax highlighting. Multi-line mode is the default.
- **Inline diff view:** if the assistant produces code that resembles a refactor of code in the user's last message, show side-by-side diff (using `react-diff-viewer`).
- **Copy-as-file button** on each code block ("save as `foo.py`").

## API

Same endpoints as chat (`/api/code/*` is just `/api/chat/*` with a different default model — implemented as a thin alias).

## Acceptance criteria

- ✅ Default opens with qwen3-coder:30b, system prompt = "You are an expert pair programmer. Be terse and produce correct code."
- ✅ Pasting 20-page Python file yields prompt-token count visible in the info pane *before* sending.
- ✅ Diff view appears when assistant's output is detectably a refactor.
- ✅ Streaming smooth even for 1 000-token outputs (qwen3-coder typical refactor).
- ✅ Switching to `gemma4:26b` mid-conversation works and warns "Coder context lost — orchestrator will see transcript only."
