<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-07 · Test Spec — Ollama integration (`LLMChat` port)

**Status:** Accepted
**Owner:** Chris
**User Spec:** [`../../use-cases/UC-07-scheduler-routing.md`](../../use-cases/UC-07-scheduler-routing.md)
**Functional Spec:** [`../functional/UC-07-scheduler-routing.md`](../functional/UC-07-scheduler-routing.md)

<!-- VAULT-SYNC: this Test Spec was a placeholder when UC-07 was pulled
into Sprint 2; the v0.2 body below was authored on develop in the
feature/UC-07-llmchat-port slice. Please mirror in the vault and
re-sync /docs at sprint review. Status was already Accepted; only
Version + body change. -->

## Approach

Two test surfaces:

1. **Unit tests against a stdlib fake.** The `OllamaLLMChat` adapter is
   exercised against a `ThreadingHTTPServer` that mimics Ollama's
   `/api/{tags,ps,chat,pull,delete}` endpoints. No real Ollama daemon
   required for `develop` merges (DP-002, DP-007). The fake is a tiny
   handler in `tests/conftest.py`, no third-party dep.
2. **Tests against `FakeLLMChat`.** Code that *consumes* the port
   (Slice A's `services/bootstrap.py` is the first such consumer; the
   chat/code/dashboard/admin routers will follow in later slices) is
   tested by injecting `FakeLLMChat`, never reaching a real socket.
   This is the seam DP-029 (hexagonal) prescribes.

A separate `pytest -m integration` suite runs the same surface against
a real Ollama on the developer's machine; it is **not** required for
`develop` merges (per `CONTRIBUTING.md`).

The wire-shape contract test pins the **exact** JSON keys the adapter
reads from Ollama. Golden payload literals in `tests/test_uc07_port.py`
record the Ollama version they were captured against. Mutating any
pinned key (rename / drop) fails the test before any user-visible
regression.

## Test cases — Slice B (this slice)

Maps to Functional Spec §Acceptance criteria + §Failure handling.

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-6 | `OllamaLLMChat.list_models()` against the fake returns `ModelInfo` for `gemma3:27b` + `qwen3-coder:30b` with `modified` parsed as `datetime` and `size_bytes` set. | auto |
| T-02 | AC-6 | `OllamaLLMChat.loaded()` against the fake returns `LoadedModel` list with `until` parsed when `expires_at` is present and `None` when absent. | auto |
| T-03 | AC-1, AC-6 | `OllamaLLMChat.chat_stream(...)` happy path: yields one or more `ChatChunk(done=False)` deltas followed by exactly one `ChatChunk(done=True)` carrying `usage_in`/`usage_out`/`*_duration_ns` extracted from the final NDJSON event. | auto |
| T-04 | AC-1, AC-6 | `OllamaLLMChat.pull_model(...)` happy path: yields a sequence of `PullProgress` items including a final `status='success'` entry. | auto |
| T-05 | AC-1, AC-6 | `OllamaLLMChat.delete_model(...)` issues `DELETE /api/delete` with body `{"name": model}` and returns `None` on 200/204. | auto |
| T-06 | (failure) | Pointing the adapter at `127.0.0.1:1` (unreachable) raises `OllamaUnreachableError` from any method that touches the network. | auto |
| T-07 | (failure) | Fake returns 500 on `/api/tags` → `OllamaResponseError(status=500, body=...)`. | auto |
| T-08 | (failure) | Fake returns 404 with `model not found` body on `/api/chat` → `OllamaModelNotFound`. | auto |
| T-09 | (failure) | Mid-stream disconnect on `/api/chat` (server closes after some deltas, before `done=true`) → `OllamaStreamAbortedError` raised by the iterator. | auto |
| T-10 | AC-3 | `FakeLLMChat.list_models()` and `.loaded()` return canned lists; `last_call` records method + args/kwargs for assertions. | auto |
| T-11 | AC-3 | `FakeLLMChat.chat_stream(...)` yields canned token sequence then a final usage-bearing chunk; `last_call` records `model`/`messages`/`options`. | auto |
| T-12 | AC-3 | `FakeLLMChat.pull_model(...)` and `.delete_model(...)` round-trip canned data and record args. | auto |
| T-13 | AC-5 | Wire-shape pin for `/api/tags`: golden payload (Ollama 0.1.x) parses correctly; mutating each pinned key (`name`, `size`, `modified_at`, `digest`) breaks the parse. | auto |
| T-14 | AC-5 | Wire-shape pin for `/api/ps`: golden payload parses correctly; mutating each pinned key (`name`, `size_vram`, `expires_at`) breaks the parse. | auto |
| T-15 | AC-1, AC-6 | Grep boundary: outside `src/cockpit/adapters/`, no Python file imports `httpx` and no Python file references the literal `http://127.0.0.1:11434` or the constant `COCKPIT_OLLAMA_URL` *as a URL it dereferences* (the env-var lookup in `config.py` / `services/bootstrap.py` is allowed). | auto |
| T-16 | AC-1 | `cockpit-admin init` (Slice A's flow) consumes `LLMChat.list_models()` via the injected `chat_factory`; the existing T-01..T-08 of UC-08 still pass. | auto (delegates to `tests/test_init.py`) |

### Out of scope this slice (lifts later)

- **chat_stream NDJSON wire-shape pinning** — the precise final-chunk key
  set (`prompt_eval_count`, `eval_count`, `prompt_eval_duration`,
  `eval_duration`, `total_duration`) is pinned in the slice that wires
  `chat_stream` into the chat router (UC-04). Slice B exercises chat_stream
  end-to-end but does not ship a per-key sensitivity test for the
  streaming chunks.
- **AC-2** ("Stopping Ollama → dashboard badge flips to 'Ollama unreachable'
  within 30 s") — UC-02 (Sprint 3) owns that.
- **AC-4** ("Per-call metrics in `messages.usage_*`") — needs the chat
  router writing `messages` rows. Lands with UC-04.

## Pass criteria

- All 16 automated cases pass on `develop` and on `main`.
- `pytest --cov=cockpit.ports.llm_chat --cov=cockpit.adapters.ollama_chat
  --cov=cockpit.adapters.fake_chat --cov-report=term-missing` shows
  ≥ 90 % line coverage on each of the three new modules.
- Slice A's existing 44-test suite (UC-08 part A) still passes after the
  bootstrap-uses-port refactor.
- Manual smoke at sprint review (Mon 2026-05-04): start a local Ollama,
  run `cockpit-admin init` against it, see the discovered models tagged
  in `model_tags`. (Same smoke as UC-08 Slice A; the swap of HTTP probe
  → LLMChat probe is invisible to the operator.)

## Tools

- pytest, pytest-cov, httpx (already in `pyproject.toml`'s `[dev]` extras).
- Stdlib `http.server.ThreadingHTTPServer` for the in-process Ollama fake.
- No third-party HTTP test framework.
