<!-- Status: Done (technical) | Version: 1.0 | Created: 2026-04-26 | Updated: 2026-04-28 -->
# UC-02 · Functional Spec — Live dashboard + model placement board

**Status:** Done (technical)
<!-- VAULT-SYNC: implementation landed on develop in feature/UC-02-dashboard. Status flipped Accepted → In Progress → Done (technical). Spec adaptations recorded inline in the implementation:
  • The Sprint 3 frontend is plain HTML + inline JS (no Next.js + dnd-kit yet — that is Sprint 4). Drag-drop is replaced with a per-card <select>. Same backend contract.
  • `last_calls` returns [] until UC-04 lands the chat router writing the `messages` table (TODO comment in routers/dashboard.py).
  • `_probe_max_context` walks contexts largest-first; spec didn't pin the search strategy. Documented in the source.
Mirror in vault and re-sync /docs at sprint review. User Acceptance pending Chris's sprint-review sign-off on Neuroforge. -->

**Depends on:** UC-01 (login), UC-07 (`LLMChat` port + `OllamaLLMChat` adapter), ADR-004 (role gate), **ADR-005 (per-model lifecycle + perf harness)**.
**Use Case:** [`../../use-cases/UC-02-dashboard-live.md`](../../use-cases/UC-02-dashboard-live.md)
**Test Spec:** [`../test/UC-02-dashboard-live.md`](../test/UC-02-dashboard-live.md)
**Bound DG:** DG-004 — block at end of file.

## Goal

A single page that gives operators ground-truth on what Ollama is doing and lets the admin shape it via a Kanban-style placement board, plus historical metrics from the perf harness on every card and an "+ Add model" affordance.

## Page layout

```
┌─ Header strip ──────────────────────────────────────────────────────────┐
│ 14:23 · Cockpit healthy · GPU 0: 14.2/24.0 GB · GPU 1: 21.7/24.0 GB     │
└─────────────────────────────────────────────────────────────────────────┘

┌─ Placement board ───────────────────────────────────────────────────────┐
│  GPU 0 (14.2/24)  │  GPU 1 (21.7/24)  │  Multi-GPU  │ On Demand │ Avail │
│  ┌──────────────┐ │ ┌──────────────┐  │            │           │       │
│  │ gemma3:27b   │ │ │ qwen3-coder  │  │   ...      │   ...     │  ...  │
│  │ chat · 16 GB │ │ │ code · 18 GB │  │            │           │       │
│  │ ⚡ 38 tps    │ │ │ ⚡ 41 tps    │  │            │           │       │
│  │ 32k ctx      │ │ │ 32k ctx      │  │            │           │       │
│  │ ⏱ cold 12 s │ │ │ ⏱ cold 14 s │  │            │           │       │
│  └──────────────┘ │ └──────────────┘  │            │           │       │
│       ...                                                       [+ Add]│
└─────────────────────────────────────────────────────────────────────────┘

┌─ Recent calls ──────────────────────────────────────────────────────────┐
│  ts | user | model | prompt | completion | gen tps | latency_ms         │
└─────────────────────────────────────────────────────────────────────────┘
```

## API

```
GET   /api/dashboard/snapshot          → { gpus, columns, models[], last_calls[], status }
GET   /api/dashboard/stream            → SSE; pushes the same shape every 5 s

POST  /api/admin/ollama/models/{model}/place                           Depends(require_role("admin"))
                                       body { placement: 'gpu0' | ... | 'available' }
                                       → 200 { applied: { keep_alive, main_gpu?, num_gpu? },
                                               loaded_now: bool }

POST  /api/admin/ollama/models/{model}/perf-test                       Depends(require_role("admin"))
                                       body { contexts?: [int] }
                                       → SSE: stage events + final result event

POST  /api/admin/ollama/models/{model}/pull                            Depends(require_role("admin"))
                                       body { model_name }
                                       → SSE: PullProgress events

DELETE /api/admin/ollama/models/{model}                                Depends(require_role("admin"))
                                       → 204

PATCH /api/admin/ollama/models/{model}/settings                        Depends(require_role("admin"))
                                       body { keep_alive_seconds?, num_ctx_default?,
                                              single_flight?, notes? }
                                       → 200
```

`/api/dashboard/snapshot` payload:

```json
{
  "gpus": [
    {"index": 0, "vram_used_mb": 14530, "vram_total_mb": 24576, "temp_c": 71, "power_w": 240},
    {"index": 1, "vram_used_mb": 22195, "vram_total_mb": 24576, "temp_c": 75, "power_w": 290}
  ],
  "columns": ["gpu0", "gpu1", "multi_gpu", "on_demand", "available"],
  "models": [
    {
      "name": "gemma3:27b", "tag": "chat", "size_bytes": 16834567168,
      "config": { "placement": "gpu0", "keep_alive_seconds": 86400,
                  "num_ctx_default": null, "single_flight": false },
      "actual": { "loaded": true, "vram_mb": 16384, "main_gpu_actual": 0,
                  "mismatch": false },
      "metrics": { "cold_load_seconds": 12.1, "throughput_tps": 38.2,
                   "max_ctx_observed": 32768, "measured_at": "2026-04-26T19:11:02Z" }
    }
  ],
  "last_calls": [],
  "status": "healthy"
}
```

## Backend logic

### Placement transition (`POST /place`)

1. Validate `placement` against the column whitelist (must match column count detected from `nvidia-smi`).
2. `UPDATE model_config SET placement = ?` for the model. Insert default row if missing.
3. Compute the call options:

   | placement | keep_alive | main_gpu | num_gpu |
   |-----------|------------|----------|---------|
   | `gpu0`..`gpuN` | `24h` | the integer | omitted (Ollama default) |
   | `multi_gpu` | `24h` | omitted | `99` |
   | `on_demand` | `0` | omitted | omitted |
   | `available` | `0` followed by an immediate one-shot generate with `keep_alive=0` to drop now | omitted | omitted |

4. Issue a one-token warm-up call via `LLMChat.chat_stream(model, messages=[{"role":"user","content":" "}], options={...})` to trigger the load with the right options. Discard the output.
5. Wait up to 10 s for `LLMChat.loaded()` to reflect the model (or to confirm it's gone for `on_demand` / `available`).
6. Compare requested GPU vs. actual: read `nvidia-smi` VRAM deltas and Ollama's `loaded[].size_vram` per GPU. If `placement.gpuN` was requested but most of the VRAM growth happened on a different GPU, set `actual.mismatch=true` and store `actual.main_gpu_actual`. (Best effort; absent telemetry → `actual.mismatch` always false.)
7. Write `admin_audit (action='model_place', target_model, details_json={old, new, applied, mismatch})`.
8. Return the resolved options and the `loaded_now` flag.

### Performance harness (`POST /perf-test`)

ADR-005 §4 step-by-step. Implementation outline:

```python
async def perf_test(model: str, contexts: list[int] | None) -> AsyncIterator[dict]:
    contexts = contexts or [4096, 16384, 32768, 65536]
    yield {"event": "stage", "data": {"name": "lock"}}
    async with model_locks[model]:                       # blocks user calls (single-flight)
        yield {"event": "stage", "data": {"name": "unload"}}
        await drop_model(model)
        await wait_until_unloaded(model, timeout=15)
        yield {"event": "stage", "data": {"name": "cold_load"}}
        gpu_before = await telemetry.sample()
        t0 = time.monotonic()
        async for chunk in llm.chat_stream(model,
                [{"role":"user","content":"Reply with: ok"}],
                options={"keep_alive": "24h"}):
            t_first_byte = time.monotonic()
            break
        cold_load = t_first_byte - t0
        gpu_after = await telemetry.sample()
        yield {"event": "stage", "data": {"name": "throughput"}}
        tps_runs = []
        for _ in range(3):
            usage = await measure_throughput(model, prompt_tokens=200, output_tokens=200)
            tps_runs.append(usage["throughput_tps"])
        yield {"event": "stage", "data": {"name": "context_probe"}}
        max_ctx = await probe_max_context(model, contexts)
        yield {"event": "stage", "data": {"name": "persist"}}
        save_model_perf(model, cold_load, mean(tps_runs), max_ctx,
                        gpu_layout_diff(gpu_before, gpu_after))
        yield {"event": "result", "data": last_perf_row(model)}
        yield {"event": "stage", "data": {"name": "restore"}}
        await restore_prior_placement(model)
```

The harness is single-flight at the host level — only one perf test at a time, enforced by an additional `host_perf_lock` so the cockpit doesn't perf-test two models at once.

### Live snapshot

Two background tasks (FastAPI lifespan):

- `gpu_sampler` — every 5 s if `Telemetry.sample()` returns non-null. Writes `metrics_snapshot`.
- `model_state_sampler` — every 30 s. Calls `LLMChat.loaded()` and `LLMChat.list_models()`. Updates an in-memory snapshot used by `/api/dashboard/snapshot`.

`/api/dashboard/stream` SSE coalesces these into a single payload at the GPU sampler cadence.

## Frontend behaviour

- React client component on `/dashboard`.
- `useDashboardStream()` hook subscribes to `/api/dashboard/stream` and updates a Zustand store.
- Placement board uses `dnd-kit` for drag-and-drop. `useDragEnd` calls `POST /api/admin/ollama/models/{model}/place`. Drag handles render only when `currentUser.role === 'admin'`.
- "+ Add model" opens a side drawer with `react-aria-components` form; submit POSTs to `/api/admin/ollama/models/{model}/pull` and consumes the SSE.
- Card hover (admin only) shows the action menu (`@radix-ui/react-dropdown-menu`).
- Card click opens the metrics-history drawer.
- VRAM bar in column header is a CSS gradient driven by `gpus[i].vram_used_mb / vram_total_mb`.

## Data model touched

- New: `model_config` (ADR-005 §1).
- New: `model_perf` (ADR-005 §4).
- Read: `users` (for recent-calls join), `messages` (recent calls + per-model metrics), `metrics_snapshot` (GPU strip).
- Write: `admin_audit` for every state-changing admin action on this page.

## Acceptance criteria

- See Use Case §Acceptance criteria. Test Spec automates each.
- The `/api/dashboard/snapshot` shape matches the schema above and is type-checked in tests.
- The placement-transition endpoint completes within 12 s for a model that fits on the target GPU (warm-up included).

## Notes

- This page absorbs what was originally split between UC-02 (dashboard) and UC-10 (admin Ollama config). UC-10 keeps the parts the placement board doesn't cover: heuristic regex editor, code-mode default system prompt, perf-test history per model, audit-log filtering.
- The "requested vs. actual" chip is the cockpit's honesty about Ollama's placement decisions. Not a bug.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes — three reads + four writes through `LLMChat`, plus `Telemetry`.

| External system | Direction | Port (core) | Adapter | Inbound / Outbound |
|---|---|---|---|---|
| `nvidia-smi` (optional) | Read | `Telemetry` (`sample() → GpuSnapshot \| None`) | `NvidiaSmiTelemetry` (`adapters/telemetry.py`) | Outbound |
| Ollama daemon — read | Read | `LLMChat` (subset: `list_models`, `loaded`) | `OllamaLLMChat` (UC-07) | Outbound |
| Ollama daemon — placement / warm-up | Write (chat_stream w/ keep_alive options) | `LLMChat.chat_stream` | `OllamaLLMChat` | Outbound |
| Ollama daemon — pull | Write (streaming) | `LLMChat.pull_model` | `OllamaLLMChat` | Outbound |
| Ollama daemon — delete | Write | `LLMChat.delete_model` | `OllamaLLMChat` | Outbound |

**Why both ports here:**

- Same `LLMChat` port for read + warm-up + pull + delete because they all hit the same Ollama daemon. DP-007 binds — splitting into `ModelInventory` + `ModelLifecycle` + `ChatStream` would force three adapters with shared backend state.
- `Telemetry` stays separate because it's a different backend (subprocess vs HTTP) with different optionality semantics.

**Test seam:** `FakeLLMChat` (UC-07) exposes a `place_calls` recorder so dashboard placement tests can assert that "drag to GPU 0" produces `chat_stream(...keep_alive=24h, main_gpu=0)`. `FakeTelemetry` returns deterministic GPU snapshots used in the "requested vs. actual" mismatch detection.

**Compliance:** DP-029 (hexagonal), DP-007 (one port per backend), DP-013 (only this router writes `model_config` / `model_perf`), DP-014 (single-flight lock = the v0.1 budget contract per model), DP-031 (drag-drop is admin-only; non-admin sees read-only board).
