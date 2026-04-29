# Claude Code prompt — UC-07 LLMChat port (this slice)

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Context

You are picking up Sprint 2 of the llm-cockpit project. Read these files first, in order, before writing a single line of code:

1. `CLAUDE.md` — project rules, stack, the Spec-First gate, branch/commit conventions.
2. `docs/process/SPRINT_STATE.md` — live sprint state.
3. `docs/specs/functional/UC-07-scheduler-routing.md` — the Accepted functional spec for this story.
4. `docs/specs/test/UC-07-scheduler-routing.md` — the test spec (currently a stub; you will fill it in).

## Three decisions already made in Cowork — do not re-litigate

**1. Status field editing**
The vault is not mounted on this machine, so `docs/` is authoritative this session (CLAUDE.md fallback rule). Edit the `Status` header in `docs/specs/functional/UC-07-scheduler-routing.md` to `In Progress` when you open the branch, and to `Done (technical)` when all tests pass. Flag the vault sync for sprint review.

**2. Truncated conversation tail**
There is no additional sign-off block to honour from the previous session. Work from the AC table below.

**3. Design — confirmed, proceed as stated**

| Decision | Detail |
|---|---|
| Port location | `src/cockpit/ports/llm_chat.py` |
| Exceptions | Defined at the port layer: `OllamaUnreachableError`, `OllamaResponseError`, `OllamaStreamAbortedError`, `OllamaModelNotFound` |
| DI seam | Thread a `chat_factory` callable (or the adapter instance itself) through `run_init` / bootstrap so the composition root swaps real vs. fake — no code outside `adapters/` opens a socket |
| Wire-shape contract | Pin `/api/tags` key set and `/api/ps` key set now; defer chat_stream NDJSON pinning to the chat_stream slice |

## Acceptance-criteria scope for this slice

| AC | Scope |
|---|---|
| No direct `OLLAMA_URL` / `httpx` calls outside `adapters/ollama_chat.py` | ✅ this slice — enforce with a grep-based test |
| Stopping Ollama → dashboard "Ollama unreachable" within 30 s | ⏭ Sprint 3 (UC-02) |
| `FakeLLMChat` is the test double | ✅ this slice |
| Per-call metrics in `messages.usage_*` | ⏭ chat_stream slice |
| Wire-shape contract test for `/api/tags` + `/api/ps` | ✅ this slice |
| chat_stream NDJSON shape pinned | ⏭ chat_stream slice |
| Adapter is the only Ollama-aware code | ✅ this slice |

## What to build

### Files to create

```
src/cockpit/ports/llm_chat.py          — Protocol + dataclasses + exception hierarchy
src/cockpit/adapters/ollama_chat.py    — OllamaLLMChat (httpx, all five methods)
src/cockpit/adapters/fake_chat.py      — FakeLLMChat (configurable stubs)
tests/test_uc07_port.py                — wire-shape contract tests + grep boundary test
```

### Port surface (copy faithfully from the functional spec §Port surface)

Use `cockpit/ports/` not `app/ports/` — the functional spec uses `app/` as a placeholder; CLAUDE.md §Directory layout is authoritative.

### Adapter

- `httpx.AsyncClient(base_url=ollama_url, timeout=httpx.Timeout(connect=5.0, read=900.0))`
- URL resolution order (highest wins): `COCKPIT_OLLAMA_URL` env → `[ollama] url` in `config.toml` → `OLLAMA_HOST` env → `http://127.0.0.1:11434`
- Implement all five methods: `list_models`, `loaded`, `chat_stream`, `pull_model`, `delete_model`

### Bootstrap wiring

`bootstrap.py` already calls `probe_ollama` which makes a raw HTTP call. Switch it to go through the port:
- Accept an `llm_chat: LLMChat` parameter (or a factory) in `run_init` / `probe_ollama`.
- Default to constructing `OllamaLLMChat` from config when called from the real CLI.
- This satisfies AC-1 (no raw Ollama calls outside the adapter).

### Test spec — fill in the stub

Before writing tests, populate `docs/specs/test/UC-07-scheduler-routing.md` with:
- Approach section (pytest + httpx, FakeLLMChat seam, wire-shape contract approach)
- Test cases derived from this slice's ACs
- Pass criteria (≥ 90 % coverage on the three new modules)

Write the tests to match what you document.

### Wire-shape contract tests

Pin the exact JSON keys the adapter reads from Ollama's responses:

- `/api/tags` response: `models[*]` with keys `name`, `size`, `modified_at`, `digest`
- `/api/ps` response: `models[*]` with keys `name`, `size_vram`, `expires_at`

Use `httpx`'s transport mocking (not a real Ollama). If Ollama renames a key in a future version, the test fails first.

### Grep boundary test

One test that `grep`s (or `ast`-walks) the source tree and asserts that no file outside `src/cockpit/adapters/` contains an import of `httpx` or a string matching `OLLAMA_URL` / `ollama_url`. This enforces AC-1 mechanically.

## Branch + commit

```
git checkout develop
git pull
git checkout -b feature/UC-07-llmchat-port
```

Commit prefix: `[UC-07]`

One PR against `develop` when functional tests pass. Fill in the PR template — the checklist requires the spec link and the DG-004 block (already in the functional spec).

## Coverage target

≥ 90 % line coverage on:
- `src/cockpit/ports/llm_chat.py`
- `src/cockpit/adapters/ollama_chat.py`
- `src/cockpit/adapters/fake_chat.py`

Run: `pytest --cov=cockpit.ports.llm_chat --cov=cockpit.adapters.ollama_chat --cov=cockpit.adapters.fake_chat --cov-report=term-missing`

## Stop and ask Chris if

- The functional spec says something that conflicts with the actual Ollama API (document the gap, propose a spec fix, wait for acceptance before continuing).
- Any ADR-level decision surfaces (new dependency, new port, schema change beyond what's specced).
- Coverage falls below 90 % and you can't see a clean path to fix it without changing the spec scope.

## Do NOT

- Implement `chat_stream` wire-shape pinning (deferred to chat_stream slice).
- Touch `main.py` or any router (those come in UC-01 / UC-09 slices).
- Advance the spec status beyond `In Progress` — only `Done (technical)` when tests pass, and Chris must confirm `User Accepted`.
