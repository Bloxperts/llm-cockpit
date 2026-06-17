<!-- Status: Draft | Version: 1.2-draft | Created: 2026-05-01 -->
# UC-02 · Functional Spec Draft — Dynamic GPU placement board v1.2

**Status:** Draft for Chris review.
**Amends:** `UC-02-dashboard-live.md` v1.1 and ADR-005.
**Motivation:** Make the dashboard the daily operating surface for preloading models onto GPU targets, spanning models across all GPUs where Ollama allows it, and tuning context / keep-alive policy with honest VRAM feedback.

## Current implementation awareness

The current cockpit already has the important bones:

- `/dashboard` is a Next.js client page backed by `/api/dashboard/snapshot` and `/api/dashboard/stream`.
- Drag and drop is already present via `@dnd-kit/core`.
- Dashboard columns are already dynamic: `gpu0..gpuN`, then `multi_gpu` only when there are at least two GPUs, then `on_demand` and `available`.
- Placement already posts to `POST /api/admin/ollama/models/{model}/place`.
- A perf-test SSE drawer already exists for one model at a time.
- `model_config` already stores `placement`, `keep_alive_seconds`, `num_ctx_default`, and `single_flight`.

This draft keeps that architecture and tightens the semantics.

## Ollama behavior to design around

Ollama exposes these useful controls per API call:

- `keep_alive`: accepts a duration string such as `10m` or `24h`, a number of seconds, any negative value for indefinite loaded state, and `0` to unload after the request.
- Empty `/api/generate` or `/api/chat` requests can preload a model; empty request plus `keep_alive=0` can unload it.
- `num_ctx` sets the requested context window for a call.
- `main_gpu` and `num_gpu` exist as model options, but one Ollama daemon does not give hard per-GPU pinning. GPU choice is best effort.
- Ollama’s documented multi-GPU behavior is: if a model fits fully on one GPU, it tends to load it on one GPU; if it does not fit, it spreads the model across available GPUs.

Implication: the UI must say “requested GPU” and “actual GPU layout”, not pretend a drag/drop move is a hard scheduler guarantee.

## Placement zones

The board columns are generated from current telemetry:

- `GPU 0`, `GPU 1`, ... `GPU N`: one column per detected GPU.
- `Cross GPU`: shown only when GPU count is at least two. This replaces the current label `Multi-GPU`, while keeping the stored value `multi_gpu` unless we choose to migrate.
- `On Demand`: installed and callable, but not intentionally kept loaded.
- `Available`: installed but parked. It is not warm and should not appear as a preferred runtime choice unless selected explicitly elsewhere.

No GPU telemetry:

- Show only `On Demand` and `Available`.
- Keep all drag/drop behavior between those two zones.
- Hide `Cross GPU`.

One GPU:

- Show `GPU 0`, `On Demand`, `Available`.
- Hide `Cross GPU`.

Three or more GPUs:

- Show `GPU 0..GPU N`, `Cross GPU`, `On Demand`, `Available`.
- The layout must remain horizontally scannable. Use a horizontally scrollable board on narrow screens rather than squeezing cards until they become unreadable.

## Drag and drop behavior

Admin users can drag any model card to any visible zone. Non-admin users see the same board read-only.

On drop:

1. Optimistically move the card into the target zone with a pending state.
2. Send `POST /api/admin/ollama/models/{model}/place`.
3. The server writes desired state to `model_config`.
4. If target is warm (`gpuN` or `multi_gpu`), preload the model with the resolved options.
5. If target is cold (`on_demand` or `available`), unload the model.
6. Refresh loaded-state, GPU telemetry, cold-load metrics, estimated max context, and actual placement.
7. If the operation fails, restore the previous card position and show a compact inline error on the board.

Accessible fallback stays mandatory: each card keeps a placement menu for keyboard and non-drag users.

## Keep-alive policy

Warm placements need an explicit retention control. The default should be visible, not hidden in code.

Recommended presets:

- `15m`
- `1h`
- `4h`
- `24h`
- `Permanent`
- `Custom`

Mapping:

- `Permanent` sends `keep_alive=-1`.
- Presets send seconds or duration strings.
- `On Demand` defaults to `keep_alive=0`.
- `Available` unloads immediately and is not used as an automatic chat/code default.

Data model:

- Keep `model_config.keep_alive_seconds` for numeric finite values.
- Add `model_config.keep_alive_mode`: `default | finite | permanent | unload`.
- Add `model_config.keep_alive_label` or derive it in the API response for display.

Reason: `NULL`, `0`, and negative values are currently not expressive enough in the database to distinguish “use default”, “unload”, and “permanent” cleanly.

## Cross GPU semantics

`Cross GPU` means: “ask Ollama to spread across all visible GPUs as far as possible.”

Implementation:

- For `multi_gpu`, send `num_gpu=99`.
- Do not send `main_gpu`.
- Keep the model warm using the selected keep-alive policy.
- After load, record actual VRAM growth per GPU and show the distribution on the card.

Important UI copy:

- Column label: `Cross GPU`.
- Tooltip: `Uses all visible GPUs when Ollama decides the model needs them. Small models may still fit on one GPU.`

## Context budget

Each model card should show two context numbers:

- `Max estimated ctx`: the cockpit’s VRAM-based estimate after the model is loaded on its actual GPU layout.
- `Ollama ctx limit`: the configured `num_ctx_default` that cockpit sends to Ollama calls, if set.

Calculation:

1. Load the model into the target placement.
2. Read actual VRAM usage before/after and current free VRAM.
3. Read model metadata from `/api/show` where available, especially architecture context length and KV-cache parameters.
4. Estimate KV-cache bytes per token.
5. Reserve headroom before calculating:
   - default 15 percent of total VRAM, minimum 1 GiB per involved GPU;
   - configurable later as an advanced admin setting.
6. Clamp the result to the model’s advertised architecture context length when available.
7. Display the estimate as a confidence value:
   - `estimated` when model metadata is sufficient;
   - `measured` when a perf/context probe has confirmed it;
   - `unknown` when metadata or telemetry is missing.

The card should make it easy to apply the estimate:

- Inline stepper/input for `Ollama ctx limit`.
- Quick actions: `Use safe estimate`, `Use measured max`, `Clear override`.
- Warn if configured `num_ctx_default` exceeds the safe estimate.

## Performance tests

Keep the current single-model perf test, but add two dimensions:

- `Run for this placement`: tests the model using its current target zone.
- `Compare placements`: tests single GPU and Cross GPU layouts where available.

Admin actions:

- Card action: `Test`.
- Board toolbar action: `Test all`.
- Optional filter for `Test all`: `missing metrics only`, `stale metrics`, `all models`.

Perf result should store:

- `model`
- `placement_tested`: `gpu0 | gpu1 | ... | multi_gpu | on_demand`
- `gpu_count_at_test`
- `gpu_layout_json`
- `cold_load_seconds`
- `first_token_ms`
- `throughput_tps`
- `max_ctx_observed`
- `num_ctx_used`
- `keep_alive_used`
- `measured_at`

Cold-load refresh:

- After a model is placed and loaded, run a lightweight cold-load refresh only if the admin explicitly asks or if there is no previous measurement.
- Do not automatically run the full perf harness on every drag/drop; that would make the board feel unpredictable and could monopolize the GPU.

## Model metadata and release date

Cards should display:

- Model name.
- Tags: `chat`, `code`, `both`.
- Size.
- Quantization and parameter size where `/api/show` exposes them.
- Context architecture limit where available.
- Release date if available.

Reality check:

- Ollama local APIs reliably expose local model metadata and local modified time, but not always an upstream release date.
- `ModelInfo.modified` from `/api/tags` is local modified/download metadata, not necessarily release date.
- `/api/show.modified_at` is model metadata but still should be labeled carefully.

Recommended display:

- `Released: 2025-08-14` when a true upstream release date is known.
- `Updated: 2025-08-14` when only registry/model modified metadata is known.
- `Local: 2026-05-01` when only local modified/download time is known.
- `Release: unknown` when none is available.

Data model:

- Add an optional `model_metadata` table keyed by model:
  - `model`
  - `parameter_size`
  - `quantization_level`
  - `architecture_context_length`
  - `capabilities_json`
  - `release_date`
  - `release_date_source`
  - `registry_updated_at`
  - `local_modified_at`
  - `metadata_refreshed_at`

The first implementation can populate this from `/api/tags` and `/api/show`; true release date can stay best-effort.

## Snapshot API changes

Extend `ModelCardPayload`:

```json
{
  "name": "qwen3-coder:30b",
  "size_bytes": 19000000000,
  "metadata": {
    "parameter_size": "30B",
    "quantization_level": "Q4_K_M",
    "architecture_context_length": 131072,
    "release_date": null,
    "release_date_label": "Local: 2026-05-01",
    "capabilities": ["completion", "tools"]
  },
  "config": {
    "placement": "gpu0",
    "keep_alive_mode": "finite",
    "keep_alive_seconds": 86400,
    "num_ctx_default": 32768,
    "single_flight": false
  },
  "actual": {
    "loaded": true,
    "vram_mb": 16384,
    "gpu_layout": {"0": 16384},
    "main_gpu_actual": 0,
    "mismatch": false
  },
  "context": {
    "max_estimated_ctx": 49152,
    "max_measured_ctx": 32768,
    "estimate_confidence": "estimated",
    "headroom_mb": 2048
  },
  "metrics": {
    "cold_load_seconds": 12.1,
    "throughput_tps": 38.2,
    "max_ctx_observed": 32768,
    "placement_tested": "gpu0",
    "measured_at": "2026-05-01T12:00:00Z"
  }
}
```

## Backend changes

LLM port:

- Add `show_model(model) -> ModelDetails`.
- Keep `list_models`, `loaded`, and `chat_stream` as they are.

Placement endpoint:

- Accept optional `keep_alive_mode`, `keep_alive_seconds`, and `num_ctx_default`.
- Resolve Ollama options from desired placement plus explicit keep-alive policy.
- Persist desired state before load.
- Load/unload via Ollama.
- Refresh actual state.
- Return applied options plus context estimate.

Telemetry:

- Preserve per-GPU VRAM growth for the model, not just guessed main GPU.
- Store actual layout on perf rows and expose latest layout on the card.

Migrations:

- `0006_model_lifecycle_v12.py`
- Add `keep_alive_mode` to `model_config`.
- Add `model_metadata`.
- Extend `model_perf` with placement/test context fields.
- Consider removing the current `model_config.placement` check constraint that only allows `gpu0..gpu3`; dynamic GPU hosts need more than four GPUs.

## UI direction

The dashboard should feel like an operations board, not a settings form.

Layout:

- Top band: compact health + GPU telemetry strip.
- Main band: placement board with fixed-width columns and horizontal scroll when needed.
- Right drawer: model details, settings, perf history.

Card design:

- Dense, clean, 8px radius or less.
- Drag handle icon button, not text.
- Primary line: model name.
- Secondary line: parameter size, quantization, local/registry date.
- Status row: loaded/idle, requested vs actual, keep-alive.
- Context row: `ctx limit / safe max`.
- Metrics row: cold load, tokens/s, measured max ctx.

Board toolbar:

- `Add model`
- `Test all`
- `Refresh metadata`
- `Show: all | loaded | missing perf | warnings`

Suggested improvement:

- Add a warnings lane or filter, not another column. Warnings include over-context, placement mismatch, missing metadata, stale perf data, and low VRAM headroom.

## Acceptance criteria

1. Board renders dynamically for 0, 1, 2, 3, and 4+ GPUs.
2. Admin can drag/drop a model to every visible zone, with keyboard/menu fallback.
3. Non-admin sees the board but cannot drag, place, delete, or test.
4. `Cross GPU` is shown only when at least two GPUs are detected.
5. Dropping into `GPU N` preloads with `main_gpu=N` and selected keep-alive.
6. Dropping into `Cross GPU` preloads with `num_gpu=99` and no `main_gpu`.
7. `Permanent` keep-alive sends a negative Ollama keep-alive value.
8. `On Demand` and `Available` unload the model.
9. Cards show actual GPU layout after load when telemetry is available.
10. Cards show max estimated context and configured Ollama context limit separately.
11. Configured context above the safe estimate shows a warning.
12. Perf test can run per model and, where requested, compare single-GPU vs Cross GPU placement.
13. `Test all` runs sequentially under the host perf lock and can be cancelled.
14. Release date is displayed when true metadata is available; otherwise the UI labels local/updated dates honestly.
15. Hosts with more than four GPUs are accepted by backend validation and database constraints.

## Open questions

- Should `Available` mean “installed but hidden from chat/code pickers”, or only “not warm”? Current naming suggests hidden/parked; we should decide.
- Should `Cross GPU` be stored as `multi_gpu` for backwards compatibility or migrated to `cross_gpu` for clarity?
- Should automatic context estimation run on every placement load, or only when the admin opens the model drawer?
- Should `Test all` include `Available` models by default? Recommendation: no; default to visible warm/on-demand candidates, with an explicit include-available toggle.
- How much VRAM headroom should be default? Recommendation: 15 percent, minimum 1 GiB per involved GPU.

## References

- Ollama FAQ, keep-alive and GPU loading behavior: https://docs.ollama.com/faq
- Ollama API usage metrics: https://docs.ollama.com/api/usage
- Ollama show model details: https://docs.ollama.com/api-reference/show-model-details
