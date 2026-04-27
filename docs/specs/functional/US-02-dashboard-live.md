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

1. **Header strip** — current time, "Stack: HEALTHY/DEGRADED/CRITICAL" badge.
2. **GPU cards × 2** — for each GPU: temp, power, util, mem used/total, throttle reason if any. Live-refreshed every 5 s.
3. **Loaded models** — table of `name | vram_gb | loaded_for | last_used`. Live-refreshed every 30 s.
4. **Scheduler stats** — counters from `GET /stats`: total / heavy / vision / light, queued_max. Live-refreshed every 60 s.
5. **Recent calls** — last 20 rows of `messages` table joined with model + latency: `ts | user | model | prompt_tok | completion_tok | gen_tps | latency_ms`. Live-tail via SSE.
6. **Health rules**:
   - HEALTHY: all GPUs &lt;80 °C, throttle &lt;5 %, scheduler reachable, ≥1 chat model loaded.
   - DEGRADED: GPU &gt;85 °C OR throttle &gt;20 % OR a pinned model evicted.
   - CRITICAL: scheduler unreachable OR Ollama down OR GPU &gt;90 °C OR memory ECC error.

## API

```
GET  /api/dashboard/snapshot   → { gpus, models, scheduler_stats, last_calls, status }
GET  /api/dashboard/stream     → SSE; push the same shape every 5 s
```

## Frontend

- React Server Component for initial render (SSR).
- Client component subscribes to `/api/dashboard/stream` for live updates.
- Recharts mini-line for GPU temp last 5 min (driven from `metrics_snapshot` table).

## Acceptance criteria

- ✅ Page loads in &lt;500 ms with a meaningful first paint.
- ✅ GPU temp updates within 5 s of an actual change observed via `nvidia-smi`.
- ✅ When a model is unloaded out-of-band (Ollama eviction), the model row disappears from the dashboard within 30 s.
- ✅ When the scheduler is stopped, status flips to DEGRADED (not CRITICAL — Ollama still works) within 30 s.
- ✅ When Ollama is stopped, status flips to CRITICAL within 60 s.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. The dashboard reads from three external systems.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| `nvidia-smi` (subprocess on Neuroforge host) | Read | `Telemetry` (e.g. `def sample() -> GpuSnapshot`) | `NvidiaSmiTelemetry` in `app/telemetry.py` | Outbound |
| Ollama daemon (`http://127.0.0.1:11434`) | Read (`/api/ps`, `/api/tags`) | `ModelInventory` (e.g. `def loaded() -> list[LoadedModel]`) | `OllamaModelInventory` in `app/ollama_client.py` | Outbound |
| Scheduler service (`http://127.0.0.1:8001`) | Read (`/stats`) | `SchedulerControl` (subset: `def stats() -> SchedulerStats`) | `SchedulerHTTP` in `app/scheduler_client.py` | Outbound |

**Which classifications were considered and rejected?**

- Direct `nvidia-smi` calls inside `routers/dashboard.py` were rejected: that mixes core logic with the OS-specific shell call and prevents unit testing the dashboard router without a real GPU. → must go behind `Telemetry`.
- Ollama via the existing `app/ollama_client.py` is an adapter; the dashboard router must consume it through the `ModelInventory` port, not the raw HTTP client.
- Scheduler stats are a *subset* of the full `SchedulerControl` port (which also covers `generate_stream`, `embed`). The dashboard binds only to the `stats()` method.

**Test seam:** all three ports must have an in-memory fake (`FakeTelemetry`, `FakeModelInventory`, `FakeScheduler`) so the dashboard router has unit tests independent of the host.

**Compliance:** DP-029 satisfied; DP-008 (escape-hatch) confirmed for Ollama adapter (replacing with vLLM later requires only a new `ModelInventory` adapter).

