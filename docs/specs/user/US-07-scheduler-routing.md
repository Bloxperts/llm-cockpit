<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# US-07 · User Spec — Ollama integration

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-07-scheduler-routing.md`](../functional/US-07-scheduler-routing.md)
**Test Spec:** [`test/US-07-scheduler-routing.md`](../test/US-07-scheduler-routing.md)
**Sprint:** 1 (architecture design) → 2 (implementation, in service of US-08 + US-01).
**Min role:** infrastructure (cross-cutting; not a user-facing screen).

> **Note on filename:** the file is named `US-07-scheduler-routing.md` for v0.1 to avoid a rename mid-sprint. The story is **Ollama integration**, per ADR-003 §4 (scheduler dependency dropped).

## Story

> As the cockpit backend I need a single, stable, well-tested way to talk to Ollama — list models, stream chat completions, query loaded-model state — so that every page (chat, code, dashboard, admin Ollama config) reads from one place and the cockpit handles Ollama outages gracefully.

## Target state

The cockpit defines one outbound port:

- **`LLMChat`** — abstract interface in the cockpit core. Methods:
  - `list_models() → list[ModelInfo]` (name, size_bytes, modified, digest)
  - `loaded() → list[LoadedModel]` (name, size_vram, until)
  - `chat_stream(model, messages, options) → AsyncIterator[Chunk]`
  - `pull_model(model) → AsyncIterator[PullProgress]` *(used by US-10 only)*
  - `delete_model(model) → None` *(used by US-10 only)*

Concrete adapter (the only one in v0.1):

- **`OllamaLLMChat`** at `app/adapters/ollama_chat.py`. Talks to `OLLAMA_URL/api/{tags,ps,chat,pull,delete}`. Re-streams Ollama's NDJSON chunks to the cockpit's chat router as SSE.

Configuration:

- `COCKPIT_OLLAMA_URL`, default `http://127.0.0.1:11434`. Fallback chain: `COCKPIT_OLLAMA_URL` → `OLLAMA_HOST` → `127.0.0.1:11434`.
- Connect / read timeouts: 5 s connect, 900 s read (long generations).

Failure modes:

- **Ollama unreachable.** Cockpit returns 503 to the chat / code routers. Dashboard flips to "Ollama unreachable" badge (US-02).
- **Ollama returns 4xx / 5xx.** Cockpit surfaces the error message verbatim with a "Retry" affordance.
- **Mid-stream disconnect.** Partial reply is saved with `usage_in / usage_out` set to whatever was extracted before the cut, plus `latency_ms` and `error="stream_aborted"`.
- **Model unknown.** The picker should not let users select a model not in `list_models()`; if it happens (race), the chat router returns 404 with `{"detail": "model not found, refresh the picker"}`.

## Acceptance criteria

1. Code-base review: no direct call to `OLLAMA_URL` from any router. All calls go through `LLMChat`.
2. Stopping Ollama makes the dashboard badge flip to "Ollama unreachable" within 30 s.
3. A canned `FakeLLMChat` (yields canned tokens in tests) is available so chat / code routers have unit tests with no real Ollama.
4. Per-call metrics extracted from Ollama's final NDJSON event are present in `messages.usage_in`, `messages.usage_out`, `messages.gen_tps`, `messages.latency_ms`.
5. The contract between cockpit and Ollama is pinned by a small "wire-shape" test in the test spec — when Ollama bumps a major version, this test fails first, before any user-visible regression.
6. The `OllamaLLMChat` adapter is the only place that knows about Ollama's specific URL paths and NDJSON wire format.

## Scope boundaries (out)

- Multiple `LLMChat` adapters in v0.1 — only `OllamaLLMChat`. Pluggable adapter machinery is v0.2.
- Embedding generation. Ollama supports `/api/embeddings` but no v0.1 story uses it; method intentionally not on the port.
- Auth / API-key support to a remote Ollama. v0.2 if anyone asks.
- Retry / circuit-breaker logic at the port level. v0.2.

## Notes

- DG-004 binding: this *is* the cockpit's main outbound boundary. The Functional Spec carries the full DG-004 block.
- Per ADR-003 §4, the AgenticBlox scheduler at port 8001 is *not* an adapter for `LLMChat` in v0.1. AgenticBlox can proxy Ollama itself; the cockpit only knows about `OLLAMA_URL`.
- Pull-progress and delete (used by US-10) are on the same port to avoid a second adapter for the same backend.
