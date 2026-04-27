<!-- Status: Draft | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-02 · Functional Spec — Dashboard live view

**Status:** Draft
**Depends on:** US-01 (login).
**User Spec:** [`../user/US-02-dashboard-live.md`](../user/US-02-dashboard-live.md)
**Test Spec:** [`../test/US-02-dashboard-live.md`](../test/US-02-dashboard-live.md)
**Bound DG:** DG-004 — see block at end of file.

## Goal

Authenticated users see a single page summarising the *current* state of the LLM stack: which models are loaded with what VRAM, GPU 0/1 temperature/power/utilization, scheduler queue stats, and a stream of the last 20 calls.

## Layout (top → bottom)

1. **Header strip** — current time, "Cockpit is HEALTHY / DEGRADED / Ollama unreachable" badge.
2. **GPU panel** — per GPU: temp, power, util, mem used/total. Live-refreshed every 5 s. **Renders empty state when `nvidia-smi` is not on PATH.**
3. **Available models** — table of every model in Ollama's `/api/tags`, with VRAM if loaded (from `/api/ps`), tag (`chat` / `code` / `both` per ADR-004), and a "use this model" button that drops into the right page. Live-refreshed every 30 s.
4. **Recent calls** — last 20 rows of `messages` table joined with model + user: `ts | user | model | prompt_tok | completion_tok | gen_tps | latency_ms`. Live-tail via SSE. **`chat` / `code` users see only their own; `admin` sees everyone's.**
5. **Health rules**:
   - **Healthy:** Ollama reachable, `nvidia-smi` either absent or all GPUs &lt; 80 °C.
   - **Degraded:** any GPU &gt; 85 °C; **or** `/api/tags` returns nothing; **or** any model in `/api/ps` shows `until=null`.
   - **Ollama unreachable:** `/api/tags` returns no response or non-2xx for &gt; 30 s.

## API

```
GET  /api/dashboard/snapshot   → { gpu, models, last_calls, status }   (admin sees full last_calls; others see own)
GET  /api/dashboard/stream     → SSE; push the same shape every 5 s
```

## Frontend

- React Server Component for initial render (SSR).
- Client component subscribes to `/api/dashboard/stream` for live updates.
- Recharts mini-line for GPU temp last 5 min (driven from `metrics_snapshot` table).

## Acceptance criteria

- ✅ Page loads in &lt; 500 ms with a meaningful first paint.
- ✅ When `nvidia-smi` is absent, the GPU panel shows the empty state and the rest of the dashboard renders.
- ✅ When `nvidia-smi` is present, GPU temp updates within 5 s of an actual change.
- ✅ When a model is unloaded out-of-band (Ollama eviction), the VRAM column on the model row clears within 30 s.
- ✅ When Ollama is stopped, the badge flips to "Ollama unreachable" within 30 s.
- ✅ A `chat` / `code` user sees only their own rows in "Recent calls"; an `admin` sees everyone's.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. The dashboard reads from two external systems.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| `nvidia-smi` (subprocess, **optional**) | Read | `Telemetry` (`sample() -> GpuSnapshot \| None`) | `NvidiaSmiTelemetry` in `app/adapters/telemetry.py`. Returns `None` if `nvidia-smi` is not on PATH; the dashboard renders the GPU empty state. | Outbound |
| Ollama daemon (default `http://127.0.0.1:11434`) | Read (`/api/tags`, `/api/ps`) | `LLMChat` (subset: `list_models()`, `loaded()`) — the same port used by chat / code (US-07) | `OllamaLLMChat` in `app/adapters/ollama_chat.py` | Outbound |

**Which classifications were considered and rejected?**

- Direct `nvidia-smi` subprocess calls inside `routers/dashboard.py` were rejected: that mixes core logic with the OS-specific shell call and prevents unit testing the dashboard router without a real GPU. → must go behind `Telemetry`.
- A separate `ModelInventory` port for `list_models()` / `loaded()` was considered and rejected. They are subset operations of `LLMChat` (US-07) and adding a second port for the same backend just to satisfy "one port per use case" violated DP-007. The dashboard depends on the read-only methods of `LLMChat`.
- Scheduler-related ports (the original SPEC-002 had a `SchedulerControl` row) are **dropped** per ADR-003 §4.

**Test seam:** both ports have in-memory fakes (`FakeTelemetry`, `FakeLLMChat`) so the dashboard router has unit tests independent of the host.

**Compliance:** DP-029 satisfied; DP-008 (escape-hatch) — replacing Ollama with another backend later requires only a new `LLMChat` adapter; DP-012 (local-first) — both adapters default to localhost; DP-007 — one port per backend, methods reused across pages.

