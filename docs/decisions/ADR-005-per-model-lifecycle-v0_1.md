<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 -->
# ADR-005 · Per-model lifecycle + performance harness in v0.1

**Status:** Accepted
**Date:** 2026-04-27
**Supersedes:** parts of ADR-003 §6 (admin scope and v0.2 deferral list)

## Context

ADR-003 §6 deferred per-model lifecycle controls — `keep_alive`, `num_ctx`, GPU placement, single-flight policy — to v0.2 ("Model Lifecycle"). v0.1 admin was reduced to user management and tag overrides.

On 2026-04-27 Chris asked for those controls back in v0.1, plus a per-model performance harness to capture cold-load time, throughput tokens-per-second, and the maximum context that the host can actually run for each model. The dashboard becomes a drag-and-drop placement board where the admin arranges models across GPU zones, an "On Demand" zone, and an "Available" zone. Adding a new model (Ollama `pull`) is reachable from the same surface.

The motivation is operational: an admin sitting in front of the cockpit needs to be able to (a) see how the host is using its GPU(s), (b) decide which models stay warm and where, (c) measure how each model behaves on this exact hardware, and (d) install new models — all without SSH'ing or editing config files.

## What stock Ollama actually supports (the honest constraint)

The cockpit talks to **one** Ollama daemon. Ollama exposes per-call options:

- `keep_alive` — duration to keep the model loaded after the call (`0`, `5m`, `24h`, `-1`).
- `num_ctx` — context window for this call.
- `num_gpu` — number of model layers to offload to GPU.
- `main_gpu` — preferred primary GPU for offload (Ollama 0.4+).

It does **not** support hard per-GPU pinning over a single daemon. `main_gpu` is a hint; if the model doesn't fit on the chosen GPU, Ollama may overflow to another. Hard pinning requires multiple daemons each launched with `CUDA_VISIBLE_DEVICES=N` — not in v0.1 scope (one Ollama daemon assumption).

Single-flight (only one inference in progress for a given model at a time) is **not** an Ollama feature; it has to be enforced by the cockpit.

## Decision

The cockpit takes ownership of per-model lifecycle policy in v0.1, with the limits above documented honestly to the admin.

### 1. Per-model configuration (`model_config` table)

```sql
CREATE TABLE model_config (
  model              TEXT PRIMARY KEY,
  placement          TEXT NOT NULL DEFAULT 'on_demand'
                       CHECK (placement IN ('on_demand', 'gpu0', 'gpu1', 'gpu2', 'gpu3',
                                            'multi_gpu', 'available')),
  keep_alive_seconds INTEGER NULL,         -- NULL = Ollama default; 0 = unload after call
  num_ctx_default    INTEGER NULL,         -- NULL = Ollama default
  single_flight      INTEGER NOT NULL DEFAULT 0,
  notes              TEXT NULL,
  updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Resolution at chat-call time:

- `keep_alive`: from row's `keep_alive_seconds`, or computed from `placement` (`on_demand` → 0; `gpu*` / `multi_gpu` → 24 h; `available` → 0). Sent on every call.
- `num_ctx`: from row's `num_ctx_default` if non-null. Otherwise omitted (Ollama decides).
- `main_gpu`: from `placement` (`gpu0` → 0, `gpu1` → 1, …); omitted for `multi_gpu` / `on_demand` / `available`.
- `single_flight`: cockpit-side `asyncio.Lock` per model; held across the streaming chat call.

### 2. The placement board (UC-02)

Visual layout: Kanban-style columns. Number of GPU columns is detected at runtime from `nvidia-smi`. Hosts with no GPU show only "On Demand" and "Available". A "Multi-GPU" column appears when the host has ≥ 2 GPUs.

| Column | Backed by | Effect on call |
|--------|-----------|----------------|
| `GPU 0` | `placement='gpu0'` | `keep_alive=24h`, `main_gpu=0`. Model preloaded on board change. |
| `GPU 1`, `GPU 2`, ... | `placement='gpuN'` | Same with appropriate `main_gpu`. |
| `Multi-GPU` | `placement='multi_gpu'` | `keep_alive=24h`, `num_gpu=99` (offload everything). Ollama spreads across visible GPUs. |
| `On Demand` | `placement='on_demand'` | `keep_alive=0`. Model loads per call, drops after. |
| `Available` | `placement='available'` | Installed but unloaded. Same wire effect as `on_demand` for chat calls but visually distinct (admin parked it). |

Drag-and-drop is **admin-only**. When admin drops a card on a column:

1. Cockpit `UPDATE model_config SET placement = ?` for that model.
2. If new placement implies "warm" (`gpu*` / `multi_gpu`), cockpit calls `LLMChat.chat_stream(model, [{role: 'user', content: ' '}], options={keep_alive: '24h', main_gpu: N})` to trigger the load and return immediately. Card shows a "loading…" spinner; resolves when `LLMChat.loaded()` reports the model.
3. If new placement implies "cold" (`on_demand` / `available`), cockpit issues a one-shot generate with `keep_alive: 0` to drop it (Ollama supports immediate unload via this idiom).
4. After the operation, the cockpit reads back `nvidia-smi` VRAM by GPU + Ollama's `/api/ps` to report **actual** placement on the card. If actual ≠ requested (Ollama overflowed), the card shows a warning chip and the admin sees a tooltip explaining the constraint.

### 3. Add Model affordance

A "+ Add model" button at the top of the board opens a side drawer:

- A free-text input (model name, e.g. `qwen3-coder:30b`).
- An "Search Ollama registry" affordance — for v0.1 this is a link to https://ollama.com/library; the cockpit does not embed registry search.
- "Pull" button → `LLMChat.pull_model(model)`. Progress streams in the drawer.
- On success, the model appears in the `Available` column with default `model_config` row created.

### 4. Performance harness ("Test performance" button)

Per-model action available to admins. Implementation:

```
POST /api/admin/ollama/models/{model}/perf-test    body { contexts: [4096, 16384, 32768, 65536]?, ... }
                                                   → SSE of progress events:
                                                       event: stage  data: { name: 'cold_load', model }
                                                       event: stage  data: { name: 'throughput', tokens: 200 }
                                                       event: stage  data: { name: 'context_probe', try: 32768 }
                                                       event: result data: { ...full result row... }
```

The harness runs synchronously on the cockpit's event loop (single-flight: only one perf test on the host at a time). For the requested model:

1. **Cold-load measurement.** If the model is loaded, unload it (`keep_alive=0` one-shot). Wait until `/api/ps` no longer lists it. Record `t_load_start`. Issue a tiny generate (1 token, prompt = `" "`). Record `t_first_byte`. `cold_load_seconds = t_first_byte - t_load_start`.
2. **Throughput measurement.** With the model now warm, issue a generate of a canned 200-token prompt and request 200 tokens of output. Read `eval_count` and `eval_duration` from the final NDJSON chunk. `throughput_tps = eval_count / (eval_duration / 1e9)`. Repeat 3 times, average.
3. **Max-context probe.** Try `num_ctx ∈ {4 096, 16 384, 32 768, 65 536}` (or admin-supplied set). For each, send a synthetic prompt of approximately that token count and a 50-token completion budget. Record success / OOM. Largest successful = `max_ctx_observed`.
4. **Persist.** Insert one row into `model_perf` with `cold_load_seconds`, `mean_throughput_tps`, `max_ctx_observed`, `gpu_layout_json` (`{gpu0_vram_growth_mb, gpu1_vram_growth_mb, ...}` from `nvidia-smi` deltas), `measured_at`.

```sql
CREATE TABLE model_perf (
  id                 INTEGER PRIMARY KEY,
  model              TEXT NOT NULL,
  measured_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cold_load_seconds  REAL,
  first_token_ms     REAL,
  throughput_tps     REAL,
  max_ctx_observed   INTEGER,
  gpu_layout_json    TEXT,
  notes              TEXT
);
CREATE INDEX idx_model_perf_model_ts ON model_perf(model, measured_at);
```

The perf harness restores the prior state at the end (re-loads the model on its previous `placement` if the placement was `gpu*` / `multi_gpu`; leaves it dropped for `on_demand` / `available`).

A perf test is **disruptive** — it occupies the GPU for ~30 s. Card shows a "test running, queue paused" badge while it runs.

### 5. Single-flight enforcement

The cockpit holds an `asyncio.Lock` per model name. Every chat / code call to model `M` acquires `locks[M]` before calling `LLMChat.chat_stream`. If `model_config(M).single_flight = 0`, the lock is the trivial unlocked one. This is the public-release-friendly equivalent of the AgenticBlox scheduler's "heavy slot" without requiring a separate process.

Per-model perf tests also acquire the same lock so they don't collide with user calls.

### 6. Where it lives in the UI

- **UC-02 dashboard** — placement board (drag-drop for admin, read-only otherwise) + GPU panel + recent calls + Ollama health badge + "+ Add model" button.
- **UC-10 admin Ollama config** — heuristic regex editor, code-mode default system prompt, deeper per-model metrics (perf-test history, drill-down), audit log. Shrinks compared to its previous draft because the placement board absorbs the everyday model-lifecycle controls.

## Consequences

**Positive**

- Admin can do all common Ollama lifecycle ops from the cockpit: place a model on a GPU, free a GPU, install a new model, drop a model, measure performance.
- The cockpit becomes meaningfully useful on the **first day** of installation — there's a reason to come back to the dashboard.
- The single-flight lock is a small, public-friendly substitute for the AgenticBlox scheduler.
- The perf harness gives admins ground-truth numbers ("on this box, gemma3:27b cold-loads in 12 s and runs at 38 tps") instead of forum lore.

**Negative**

- v0.1 grows. Three new tables (`model_config`, `model_perf`, `audit` already existed). Two more endpoints (perf-test, place). One new background concept (per-model lock).
- Hard GPU pinning is not actually deliverable with one Ollama daemon; the UX has to be honest about that. Cards show "requested vs actual" placement and the admin sees the truth.
- The performance harness is disruptive (~30 s per run). We document this and the cockpit only allows one perf test at a time.
- Sprint 2 just got slightly larger because UC-08 is now followed by the placement-board work; we may push that to Sprint 3.

**Neutral**

- DP-014 (governance) regains its v0.1 surface area — single-flight is a real budget contract per model.
- DP-007 (simplicity) is at risk; the placement board is more UI than the rest of the cockpit combined. Mitigated by the static-export Next.js shape and the rule that the backend is the only enforcement point.

## Compliance

- DP-007 (simplicity) — held by aggressively keeping the data model small (two new tables) and putting all the Kanban smarts in the frontend.
- DP-008 (escape-hatch) — `LLMChat` port is unchanged; the cockpit's enforcement (locks, placement) is in the cockpit core, not in the adapter.
- DP-014 (governance & budget contracts) — single-flight per model is the v0.1 contract.
- DP-029 (hexagonal) — perf harness uses `LLMChat` + `Telemetry` ports; no new outbound surface.
- DP-031 (progressive autonomy) — drag-drop is admin-only; non-admin sees the board read-only.

## Follow-up

- UC-02 (live dashboard) rewritten to make the placement board the primary visual.
- UC-10 (admin Ollama config) trimmed: keeps deeper metrics and audit log, defers everyday lifecycle to UC-02.
- ADR-003 §6 superseded in this part. The original phrase "model lifecycle moves to v0.2" no longer applies; "Model Lifecycle" as a separate v0.2 epic is replaced by ADR-005's v0.1 scope.
- The perf-test schema + the SSE event shape is part of UC-10's Functional Spec; placement board UX is in UC-02's.
