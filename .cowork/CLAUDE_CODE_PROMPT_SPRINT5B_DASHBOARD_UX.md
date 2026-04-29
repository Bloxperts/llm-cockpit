# Claude Code prompt — Sprint 5b: Dashboard GPU / model card improvements

Paste this verbatim. Can be built on the same `feature/UC-chat-ux-improvements` branch
(append commits) or as a separate `feature/dashboard-gpu-ux` branch — your choice based
on whether Sprint 5 is already merged.

---

## Read first

1. `CLAUDE.md`
2. `frontend/src/app/dashboard/page.tsx` — GPU card rendering (the `gpus` array section).
3. `frontend/src/lib/dashboard-types.ts` — `GpuPayload`, `ModelCardPayload` types.
4. `src/cockpit/routers/dashboard.py` — snapshot endpoint.
5. `src/cockpit/services/metrics.py` — `assemble_dashboard_snapshot()` — check what
   `model_card.config` already exposes.

---

## Change 1 — GPU temperature: status badge instead of colour scale

**Replace** the current VRAM/temp gradient bar with a four-level status badge.

### RTX 3090 thresholds (Ampere GPU Boost 4.0 — source of truth for this codebase)

| Status | Range | Tailwind badge colour | Rationale |
|--------|-------|----------------------|-----------|
| Good | ≤ 70 °C | `bg-emerald-500` | Full boost clock, no thermal pressure |
| Workload | 71–82 °C | `bg-sky-500` | Normal sustained compute load, no throttle |
| Throttling | 83–89 °C | `bg-amber-500` | GPU Boost starts clock reduction at ~83 °C |
| Critical | ≥ 90 °C | `bg-rose-600` | Approaching TjMax 93 °C, shutdown risk |

These thresholds are hard-coded constants at the top of `dashboard/page.tsx`:

```typescript
const GPU_TEMP_THRESHOLDS = [
  { max: 70, label: "Good",       cls: "bg-emerald-500 text-white" },
  { max: 82, label: "Workload",   cls: "bg-sky-500 text-white"     },
  { max: 89, label: "Throttling", cls: "bg-amber-500 text-white"   },
  { max: Infinity, label: "Critical", cls: "bg-rose-600 text-white" },
] as const;

function gpuTempStatus(tempC: number | null) {
  if (tempC === null) return null;
  return GPU_TEMP_THRESHOLDS.find(t => tempC <= t.max)!;
}
```

Render: a small pill badge `<span className={...}>Good</span>` next to the °C value.
Show the raw °C value as well so operators always have the number. If `temp_c` is
`null` (no telemetry), render nothing (no badge, no dash).

Remove the gradient bar entirely — it was a placeholder.

---

## Change 2 — Watts: show current / max TDP

**Current**: `152.9 W`
**Target**: `152.9 W / 350 W`

### Max TDP source

Add a `gpu_max_tdp_w` field to the `GpuPayload` TypeScript type. Backend: add it to
the `GpuSnapshot` dataclass (in `src/cockpit/ports/telemetry.py`) as an optional
`int | None` field, populated by reading `nvidia-smi` `power.limit` column.

Update the `nvidia-smi` query in `src/cockpit/adapters/telemetry.py` to add the
`power.limit` column:

```
nvidia-smi --query-gpu=index,memory.used,memory.total,temperature.gpu,power.draw,power.limit \
           --format=csv,noheader,nounits
```

Parse the new column as `max_power_w: int | None` (same `[N/A]` handling as other
nullable columns). Add it to `GpuSnapshot` and propagate through
`assemble_dashboard_snapshot()` → `GpuPayload` → frontend.

Frontend: render `{power_w?.toFixed(0)} W / {max_power_w ?? 350} W` — fall back to
`350` if `max_power_w` is null (RTX 3090 factory TDP). Colour the current value with
the same threshold logic as temperature but keyed on percentage of max:
- ≤ 70 %: `text-emerald-600`
- 71–90 %: `text-amber-600`
- > 90 %: `text-rose-600`

---

## Change 3 — Model card: show max context

Each model card in the placement board should show the configured (or default) context
window so admins can see at a glance how much VRAM budget the model needs.

### What to display

```
gemma3:27b    [loaded]   ctx 8 192
```

or, if not configured:

```
mistral:7b    [idle]     ctx —
```

### Data source

`num_ctx_default` is already in `model_config` and propagated into the `ModelCard`
payload via `assemble_dashboard_snapshot()` → `config.num_ctx_default`. Confirm it
is already present in the TypeScript `ModelConfigPayload` type; if not, add it.

Frontend: in the model card component, render a small line:
`ctx {config.num_ctx_default?.toLocaleString() ?? "—"}` in `text-xs text-neutral-500`.

Position it below the model name/tag line, above the metrics row.

---

## Migration / backward compatibility

- `GpuSnapshot` gains `max_power_w: int | None`. All existing tests use `FakeTelemetry`
  which constructs `GpuSnapshot` directly — update `FakeTelemetry` and any fixture
  that builds a `GpuSnapshot` to pass `max_power_w=None` (the field is optional, so
  `None` is valid and tests don't need to simulate a real value).
- `MetricsSnapshot` DB table does **not** need a new column — `max_power_w` is a
  live reading only (it doesn't change unless the user changes the power cap), not
  worth persisting per sample.
- Add `max_power_w` to the `_serialize_gpu` helper in `services/metrics.py` so it
  flows through to the snapshot payload.

---

## Tests

Update `tests/test_uc02_telemetry.py`:
- Parse a CSV row that includes the `power.limit` column — verify `max_power_w` is
  populated correctly.
- A row with `[N/A]` in the `power.limit` column → `max_power_w = None`.

---

## Commit + PR

```bash
git add frontend/src/app/dashboard/page.tsx \
        frontend/src/lib/dashboard-types.ts \
        src/cockpit/ports/telemetry.py \
        src/cockpit/adapters/telemetry.py \
        src/cockpit/services/metrics.py \
        tests/test_uc02_telemetry.py

git commit -m "[ux] dashboard: GPU temp status badge, watts/TDP, model ctx display"

gh pr create \
  --base develop \
  --head feature/dashboard-gpu-ux \
  --title "[ux] Dashboard: GPU status badge, watt TDP, model context" \
  --body "- GPU temperature: four-level status badge (Good/Workload/Throttling/Critical) with RTX 3090 thresholds baked in; raw °C still shown
- Watts: current / max TDP (read power.limit from nvidia-smi; fallback 350 W)
- Model cards: show configured num_ctx_default so admins see context budget at a glance"

gh pr merge --squash \
  --subject "[ux] Dashboard: GPU status badge, watt TDP, model context" \
  --delete-branch=false
```

---

## Stop and ask Chris if

- `nvidia-smi` on Neuroforge returns a different column name for `power.limit` —
  run `nvidia-smi --help-query-gpu | grep power` to confirm the exact field name.
- The `GpuSnapshot` dataclass is also used in places that would need updating beyond
  what's listed here.
