<!-- Status: Draft | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-07 · Functional Spec — Scheduler routing

**Status:** Draft
**Depends on:** the existing scheduler at `127.0.0.1:8001` (AgenticBlox LL-015).
**User Spec:** [`../user/US-07-scheduler-routing.md`](../user/US-07-scheduler-routing.md)
**Test Spec:** [`../test/US-07-scheduler-routing.md`](../test/US-07-scheduler-routing.md)
**Bound DG:** DG-004 — see block at end of file.

## Goal

The cockpit MUST NOT call Ollama directly. Every LLM call goes through the queue layer so:

- Single-flight semantics for heavy reasoning + vision are enforced.
- Per-call metrics (token counts, latency) are captured in one place.
- The 32 k cap (or whatever the active baseline is) is enforced uniformly.

## Implementation

The backend has one client class, `app/scheduler_client.py`:

```python
class SchedulerClient:
    def __init__(self, base_url="http://127.0.0.1:8001"):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=900)

    async def generate_stream(self, *, model: str, prompt: str, options: dict) -> AsyncIterator[bytes]:
        body = {"model": model, "prompt": prompt, "stream": True, "options": options}
        async with self._client.stream("POST", "/v1/generate", json=body) as r:
            async for chunk in r.aiter_bytes():
                yield chunk

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        r = await self._client.post("/v1/embed", json={"model": model, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]
```

The chat / code routers consume this client. Direct `httpx` to Ollama is forbidden in the chat / code paths.

The admin path (SPEC-006) is allowed to talk to Ollama directly because it's manipulating the daemon, not running inference.

## Failure modes

- **Scheduler unreachable** → backend returns 503 to the cockpit, dashboard flips to DEGRADED.
- **Scheduler returns 5xx** → backend surfaces the error message to the user verbatim with a "Retry" button.
- **Heavy slot busy** → request queues server-side; UI shows "Waiting in heavy slot, position 1" with elapsed time.

## Metrics extraction

For each call routed through the scheduler, the backend extracts (from the final SSE event from Ollama):

- `prompt_eval_count`
- `eval_count`
- `prompt_eval_duration` / `eval_duration` → derive prompt_tps + gen_tps
- `total_duration` → wall time

These land in `messages.usage_in / usage_out / latency_ms / gen_tps` and feed the dashboard.

## Acceptance criteria

- ✅ Codebase review: no direct call to `127.0.0.1:11434` from any chat or code router.
- ✅ Killing the scheduler kills chat (within 30 s, dashboard goes DEGRADED).
- ✅ When the scheduler's `HEAVY_SLOT` is held by user A's `deepseek-r1:32b` call, user B's request to the same model queues — measured wait time visible in the UI.
- ✅ Per-call metrics for the last 50 calls are present in the dashboard's "Recent calls" panel.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. This story *is* the canonical boundary crossing — every chat / code call leaves the cockpit core and reaches the scheduler.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| Scheduler service (`/v1/generate`, `/v1/embed`, `/stats`) | Read + write | `LLMChat` (full surface: `generate_stream`, `embed`, `stats`) | `SchedulerHTTP` in `app/scheduler_client.py` | Outbound |

**Why the cockpit must NOT call Ollama directly from the chat / code paths:**

- Single-flight semantics for HEAVY / VISION slots are enforced *only* in the scheduler. A direct Ollama call bypasses that.
- The scheduler is where the 32 k context-cap policy, per-agent `num_ctx` policy, and `keep_alive` heuristics live. The cockpit must honour those policies, not duplicate them.
- AgenticBlox LL-015 (Ollama default placement unsuitable) and LL-008 (LiteLLM proxy cannot be a load-bearing wall) both feed this rule.

**Test seam:** `FakeLLMChat` (in-memory async iterator that yields canned tokens) is the substitute used in unit tests of the chat / code routers. The cockpit's CI must include a contract test that pins the wire shape between the cockpit and the scheduler so the scheduler team can refactor internally without breaking the cockpit.

**Compliance:** DP-029 satisfied; DP-008 (escape-hatch) — the same `LLMChat` port could front a different scheduler if AgenticBlox replaces the queue layer; DP-014 (governance) — heavy-slot enforcement lives in the scheduler, not the cockpit; DP-002 (debuggability) — every chat / code call writes to `messages` with usage + latency, sourced from the scheduler's response.

