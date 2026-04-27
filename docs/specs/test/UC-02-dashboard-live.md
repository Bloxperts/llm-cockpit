<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-02-dashboard-live · Test Spec — Live dashboard + placement board

**Status:** Accepted
**Owner:** Chris
**Use Case:** [`../../use-cases/UC-02-dashboard-live.md`](../../use-cases/UC-02-dashboard-live.md)
**Functional Spec:** [`../functional/UC-02-dashboard-live.md`](../functional/UC-02-dashboard-live.md)

<!-- VAULT-SYNC: this Test Spec body was filled in on develop in feature/UC-02-dashboard
as the first commit of Sprint 3 (per the runbook in CLAUDE_CODE_PROMPT_SPRINT3_UC02.md).
Status flipped Draft → Accepted; version stays at 0.1. Mirror in vault and re-sync /docs
at sprint review. -->

## Approach

Two test surfaces:

1. **Unit tests against in-memory fakes.** All routers and services are tested
   with `FakeLLMChat` + `FakeTelemetry` + an in-memory SQLite session
   (`upgrade_to_head` against a temp data dir). No real Ollama, no real GPU.
   `httpx.MockTransport` exists in UC-07's suite and is reused only for
   adapter-level wire-shape regression tests, not for dashboard logic.
2. **Integration tests** (`pytest -m integration`) against a real Ollama on
   the developer's machine, exercising the placement transition and the
   perf harness end-to-end. Marked `slow`. **Not required for `develop`
   merge** per UC-07's precedent and `CONTRIBUTING.md`.

The Telemetry adapter (`NvidiaSmiTelemetry`) is tested with subprocess
mocking (`asyncio.create_subprocess_exec` patched to return canned stdout +
exit codes). The fake adapter handles every edge case the real `nvidia-smi`
produces (`[N/A]` columns, missing binary, non-zero exit).

The dashboard `/api/dashboard/snapshot` shape is type-checked against a
Pydantic schema (`DashboardSnapshot`) — pinned in `tests/test_uc02_dashboard.py`
so that a future divergence between the spec's JSON shape and the router's
output fails the test before any user-visible regression.

## Test cases

Reference: UC-02 functional spec §API + §Backend logic + §Acceptance criteria.

### Telemetry port + NvidiaSmiTelemetry adapter

| ID | Maps to | Description | Method |
|----|---------|-------------|--------|
| T-01 | DG-004 row 1 | Adapter parses canonical `nvidia-smi --query-gpu=...` CSV output into a `list[GpuSnapshot]` with the correct field types. | auto |
| T-02 | DG-004 row 1 | `temp_c` and `power_w` resolve to `None` when the column literal is `[N/A]`; integer columns still parse. | auto |
| T-03 | ADR-003 §5 | When `nvidia-smi` is not on PATH, `sample()` returns `None` (not an exception). | auto |
| T-04 | failure mode | When `nvidia-smi` is on PATH but exits non-zero, `sample()` raises `TelemetryUnavailableError`. | auto |
| T-05 | spec test seam | `FakeTelemetry` returns the configured static list and records every `sample()` call in `last_call`. | auto |

### Dashboard snapshot + SSE stream

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-10 | AC-1 | `GET /api/dashboard/snapshot` returns a payload that validates against the `DashboardSnapshot` Pydantic schema (matches the JSON in the spec verbatim). | auto |
| T-11 | AC-2 | When `Telemetry.sample()` returns `None`, `gpus` is `[]` and `columns` does not include any `gpuN` rung. The remaining columns (`multi_gpu`, `on_demand`, `available`) still appear, **except `multi_gpu` collapses** when there are < 2 GPUs. | auto |
| T-12 | AC-2 | Two GPUs present → columns are `["gpu0", "gpu1", "multi_gpu", "on_demand", "available"]`. One GPU → `["gpu0", "on_demand", "available"]`. | auto |
| T-13 | UC-02 §last_calls | `last_calls` is `[]` until the `messages` table lands in UC-04 (with `# TODO UC-04` in the source). | auto |
| T-14 | F-spec §status | `status` is `"healthy"` when both samplers report fresh data, `"degraded"` when GPU sampler errored once but the model sampler is fine, `"ollama_unreachable"` when the model sampler has been failing > 30 s. | auto |
| T-15 | AC-1 | `/api/dashboard/snapshot` requires `current_user_must_be_settled` — 401 without a cookie, 409 with `must_change_password=true`. | auto |
| T-16 | AC-9 | Logged-in `chat` user sees an empty `models` list when no models are tagged for their role; admin sees every model regardless of tag. (For now, every logged-in user sees the same `models` list — role-based filtering of `last_calls` is wired here, not models.) | auto |
| T-17 | UC-02 §SSE | `GET /api/dashboard/stream` is an SSE endpoint that emits at least one `data:` event within 1 s of connect; closing the client cleanly cancels the streaming task. | auto |

### Admin Ollama router — placement transition

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-20 | AC-3 | `POST /api/admin/ollama/models/{model}/place` with `placement="gpu0"` calls `LLMChat.chat_stream(...)` once with `options={'keep_alive': '24h', 'main_gpu': 0}` (assert via `FakeLLMChat.calls`). | auto |
| T-21 | AC-3 | After place, `model_config.placement` is `"gpu0"` (upserted). `admin_audit` row written with `action='model_place'` and `details_json` containing `{old, new, applied, mismatch}`. | auto |
| T-22 | spec table | `placement="multi_gpu"` → options `{'keep_alive': '24h', 'num_gpu': 99}`, no `main_gpu`. | auto |
| T-23 | spec table | `placement="on_demand"` → options `{'keep_alive': 0}`, no `main_gpu`, no `num_gpu`. | auto |
| T-24 | spec table | `placement="available"` → options `{'keep_alive': 0}` (the immediate one-shot drop). | auto |
| T-25 | F-spec §validate | `placement="gpuN"` for a host with no detected GPUs returns 422. | auto |
| T-26 | AC-4 | `actual.mismatch=true` is set when `Telemetry.sample()` shows VRAM growth on a different GPU than requested. | auto |
| T-27 | AC-5 | Non-admin users get 403 on every endpoint under `/api/admin/ollama/...`. | auto |
| T-28 | ADR-005 §5 | Concurrent `POST /place` for the same model serialises through `app.state.model_locks[model]` (single-flight). Asserted by checking that the FakeLLMChat's `calls` list shows non-overlapping start/end timestamps. | auto |

### Admin Ollama router — perf harness

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-30 | AC-6 | `POST /api/admin/ollama/models/{model}/perf-test` emits SSE `stage` events in order: `lock`, `unload`, `cold_load`, `throughput`, `context_probe`, `persist`, then a `result` event. Final `stage` event is `restore`. | auto |
| T-31 | AC-6 | A `model_perf` row is written on completion with `cold_load_seconds`, `throughput_tps`, `max_ctx_observed`, `gpu_layout_json`. `measured_at` is set. | auto |
| T-32 | AC-7 | After perf test, the model is restored to its prior `model_config.placement`. If prior was `on_demand`/`available`, the model is unloaded; if `gpuN`/`multi_gpu`, it's reloaded. | auto |
| T-33 | F-spec §host_perf_lock | Two perf-tests against different models cannot overlap — second waits for first via `app.state.host_perf_lock`. | auto |
| T-34 | F-spec §probe_max_context | `probe_max_context` walks the contexts list from largest to smallest, returning the first that succeeds. Documented assumption (binary-search variant) noted in the source. | auto |

### Admin Ollama router — pull / delete / settings

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-40 | AC-8 | `POST /api/admin/ollama/models/{model}/pull` streams `LLMChat.pull_model()` SSE events; on `status='success'` a default `model_config` row is created if missing. `admin_audit` row with `action='model_pull'`. | auto |
| T-41 | UC-02 §card actions | `DELETE /api/admin/ollama/models/{model}` calls `LLMChat.delete_model`, drops the `model_config` row, and writes `admin_audit(action='model_delete')`. Returns 204. | auto |
| T-42 | UC-02 §card actions | `PATCH /api/admin/ollama/models/{model}/settings` updates `keep_alive_seconds`, `num_ctx_default`, `single_flight`, `notes` — only the fields present in the body. Writes `admin_audit(action='model_settings_patch')`. | auto |

### GPU sampler + ModelStateSampler (services/metrics)

| ID | Maps to | Description | Method |
|----|---------|-------------|--------|
| T-50 | F-spec §gpu_sampler | `GpuSampler.sample_once(session, telemetry)` writes one `MetricsSnapshot` row per GPU on success; writes nothing when telemetry returns `None`. | auto |
| T-51 | F-spec §model_state_sampler | `ModelStateSampler.sample_once(chat)` populates the in-memory `model_state` dict with `loaded` + `available` keys. | auto |
| T-52 | F-spec §lifespan | The samplers are wired into the lifespan and run at the cadence configured (5 s / 30 s); their lifecycle (start + cancel-on-shutdown) is exercised end-to-end via TestClient context manager. | auto |
| T-53 | services/audit | `write_admin_audit()` inserts an `AdminAudit` row with the right shape; `details_json` is JSON-serialisable. | auto |

## Pass criteria

- All automated cases above pass on `develop` and on `main`.
- `pytest --cov` ≥ 90 % on each of:
  - `cockpit/ports/telemetry.py`
  - `cockpit/adapters/telemetry.py`
  - `cockpit/routers/dashboard.py`
  - `cockpit/routers/admin_ollama.py`
  - `cockpit/services/metrics.py`
  - `cockpit/services/audit.py`
- The full prior test suite (135 collected, 134 pass + 1 deferred chat_stream NDJSON skip) stays green.
- Manual smoke at sprint review (Mon 2026-05-04, on Neuroforge with real Ollama + 2× RTX): clean install → init → serve → log in as admin → drag `gemma3:27b` from Available to GPU 0 → card settles within ~10 s with `keep_alive=24h, main_gpu=0` applied → "Test performance" runs to completion and writes a `model_perf` row → "Delete" removes the model.

## Out of scope this slice (lifts later)

- React/dnd-kit drag-and-drop frontend — Sprint 4 (Next.js shell). Sprint 3
  ships a plain-HTML read-only board with a `<select>`-based admin placement
  control, no drag-drop.
- `last_calls` persistence in the snapshot — UC-04 (chat router) writes the
  `messages` rows; until then the field is `[]` with a `# TODO UC-04` comment.
- Per-model conversation count / metrics drawer — Sprint 5 (UC-03 dashboard
  history).
- Hard per-GPU pinning — out of scope per UC-02 §Scope boundaries.

## Tools

- pytest, pytest-cov (already in `[dev]`).
- `unittest.mock.AsyncMock` for `asyncio.create_subprocess_exec` mocking.
- `FakeLLMChat` (UC-07) + `FakeTelemetry` (introduced in this slice).
- `EventSourceResponse` from `sse-starlette` (already in deps).
