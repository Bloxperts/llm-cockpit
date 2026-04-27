<!-- Status: Review | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-07 · Functional Spec — Ollama integration (`LLMChat` port)

**Status:** Review
**Depends on:** ADR-003 §4 (drops the scheduler), ADR-002 v1.1 (pip-distributable shape).
**User Spec:** [`../user/US-07-scheduler-routing.md`](../user/US-07-scheduler-routing.md)
**Test Spec:** [`../test/US-07-scheduler-routing.md`](../test/US-07-scheduler-routing.md)
**Bound DG:** DG-004 — main outbound boundary in v0.1. Block at end of file.

> **History note:** filename retained as `US-07-scheduler-routing.md` to avoid a rename mid-sprint. The original SPEC-007 wired the cockpit through an AgenticBlox scheduler at port 8001; per ADR-003 §4 that dependency is dropped. v0.1 talks directly to Ollama.

## Goal

Define a single outbound port `LLMChat` that every chat / code / dashboard / admin Ollama-config surface uses to talk to Ollama. The port is the one place the cockpit cares about Ollama's wire format. Tests and chaos drills go through fakes of this port.

## Port surface

```python
# app/ports/llm_chat.py

@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_bytes: int
    modified: datetime
    digest: str

@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_vram: int
    until: datetime | None       # None means "no keep-alive ttl"

@dataclass(frozen=True)
class ChatChunk:
    delta: str                    # token text (may be empty)
    done: bool
    usage_in: int | None = None   # only on done=True
    usage_out: int | None = None
    eval_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    total_duration_ns: int | None = None

@dataclass(frozen=True)
class PullProgress:
    status: str
    digest: str | None = None
    total: int | None = None
    completed: int | None = None

class LLMChat(Protocol):
    async def list_models(self) -> list[ModelInfo]: ...
    async def loaded(self) -> list[LoadedModel]: ...
    async def chat_stream(self, *, model: str,
                          messages: list[dict],
                          options: dict | None = None) -> AsyncIterator[ChatChunk]: ...
    async def pull_model(self, model: str) -> AsyncIterator[PullProgress]: ...
    async def delete_model(self, model: str) -> None: ...
```

## Adapter (the only one in v0.1)

`app/adapters/ollama_chat.py` — `OllamaLLMChat`:

- Built on `httpx.AsyncClient(base_url=ollama_url, timeout=httpx.Timeout(connect=5.0, read=900.0))`.
- `list_models()` → `GET /api/tags`.
- `loaded()` → `GET /api/ps`.
- `chat_stream()` → `POST /api/chat` with `stream: true`. Re-emits each NDJSON chunk as a `ChatChunk` with `delta` and (on the final chunk) `usage_*` extracted from `prompt_eval_count`, `eval_count`, durations.
- `pull_model()` → `POST /api/pull` (streaming NDJSON of progress).
- `delete_model()` → `DELETE /api/delete` body `{"name": model}`.

Configuration resolution (highest precedence wins):

1. `COCKPIT_OLLAMA_URL` env.
2. `[ollama] url` in `config.toml`.
3. `OLLAMA_HOST` env (Ollama's own convention).
4. Default `http://127.0.0.1:11434`.

## Failure handling

- **Connection refused / DNS failure / 5 s connect timeout** → caller sees `OllamaUnreachableError`. Routers translate to HTTP 503. Dashboard badge flips per US-02.
- **HTTP 4xx / 5xx from Ollama** → caller sees `OllamaResponseError` carrying status + body. Routers surface to the user with the body text.
- **Mid-stream disconnect** → the iterator raises `OllamaStreamAbortedError`. The chat router persists what it received, sets `messages.error = 'stream_aborted'`.
- **Ollama returns 404 "model not found"** → `OllamaModelNotFound`. Router returns 404 to the cockpit client with `{"detail": "model not found, refresh the picker"}`.

## Wire-shape contract test

A small test in the Test Spec pins the **exact** keys we extract from Ollama's NDJSON: `model`, `created_at`, `message.role`, `message.content`, `done`, `prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `eval_duration`, `total_duration`. If Ollama bumps a major version that renames any of these, the test fails first — before any user-visible regression.

## Test seam

`app/adapters/fake_chat.py` — `FakeLLMChat`:

- `list_models()` returns a configurable static list.
- `loaded()` returns a configurable static list.
- `chat_stream()` yields a configurable token sequence with a final usage-bearing chunk.
- `pull_model()` yields a configurable progress sequence.
- `delete_model()` records the delete and asserts it was the expected model.

This is the dependency injected into chat / code routers in unit tests.

## Acceptance criteria

- ✅ Codebase review: no `httpx` direct calls to `OLLAMA_URL` outside `app/adapters/ollama_chat.py`.
- ✅ Stopping Ollama → dashboard badge flips to "Ollama unreachable" within 30 s.
- ✅ `FakeLLMChat` is the test double in unit tests for chat, code, dashboard, and admin routers.
- ✅ Per-call metrics are present in `messages.usage_in`, `messages.usage_out`, `messages.gen_tps`, `messages.latency_ms` for every call routed through the port.
- ✅ Wire-shape contract test passes against Ollama 0.x.y for whatever `y` we ship against.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. This story *is* the cockpit's main outbound boundary in v0.1.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| Ollama daemon (default `http://127.0.0.1:11434`) | Read + write | `LLMChat` (full surface above) | `OllamaLLMChat` in `app/adapters/ollama_chat.py` | Outbound |

**Why one port for everything Ollama-related:**

- `list_models()` and `loaded()` could live on a separate `ModelInventory` port. We rejected splitting them out: every existing call site already needs `chat_stream`, and a second port for the same backend just adds boilerplate without gaining isolation. DP-007 ("simplicity over elegance") binds.
- `pull_model()` and `delete_model()` are admin-only actions (US-10) but still go through the same port — same backend, same wire format, same fake.
- Per ADR-003 §4 there is **no** scheduler adapter in v0.1. If a future v0.2 wants queue semantics, a second adapter (`SchedulerHTTPLLMChat`) plugs in and the chat router's dependency injection picks one based on config.

**Test seam:** `FakeLLMChat` (above) is the only substitute used in routers' unit tests. Integration tests against a real Ollama run as a separate `pytest -m integration` suite that is **not** required for `develop` merges (see CONTRIBUTING).

**Compliance:** DP-029 (hexagonal) — port + adapter pattern; DP-008 (escape-hatch) — second backend plugs in without rewriting routers; DP-007 (simplicity) — one port per backend; DP-002 (debuggability) — every chat / code call writes a row to `messages` with usage + latency sourced from the adapter; DP-014 (governance) — n/a in v0.1 since there is no queue layer to govern.
