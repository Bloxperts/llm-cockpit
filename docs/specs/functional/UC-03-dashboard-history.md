<!-- Status: Done (technical) | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-28 -->
# UC-03 · Functional Spec — Dashboard historical view

**Status:** Done (technical)
**Depends on:** UC-02 (live dashboard, shares the metrics tables).
**User Spec:** [`../../use-cases/UC-03-dashboard-history.md`](../../use-cases/UC-03-dashboard-history.md)
**Test Spec:** [`../test/UC-03-dashboard-history.md`](../test/UC-03-dashboard-history.md)
**Bound DG:** none expected (reads from local `metrics_snapshot` and `messages` tables only); revisit if any external store is added.

## Goal

A second tab on the dashboard shows time-series for the last 24 h and 7 d of GPU temperature, VRAM used, scheduler call rate, and per-model call counts. Plus the per-user / per-model context-size distribution that US-V01 needs.

## Charts (24 h tab)

- **GPU 0 / GPU 1 temperature** — line chart, 1-min buckets.
- **VRAM used per GPU** — stacked area, 1-min buckets (one stack per loaded model).
- **Calls per minute** — bar chart with stacked agent (light/heavy/vision).
- **Latency p50/p95** — line chart per model.

## Charts (7 d tab)

- Same as above but 1-h buckets.
- **Prompt-token distribution** — histogram per agent / per model. Drives US-V01.
- **Daily completion-token total** — bar chart, drives "did we exceed our compute budget today?" (relevant when monetisation kicks in).

## Backend

```
GET /api/dashboard/history?range=24h|7d&metric=gpu_temp|vram|calls|latency|tokens
```

Returns a JSON array of `{ ts, value }` already bucketed server-side. Down-sampling: 24 h at 1-min, 7 d at 1-h.

## Storage

- `metrics_snapshot` is the source of truth — already populated by the 5 s sampler.
- Down-sampling job runs every hour: takes the last hour of 5 s samples, computes 1-min buckets (avg + p95) for `metrics_snapshot_minute` table.
- Daily job: 1-min buckets → 1-h buckets in `metrics_snapshot_hour`.
- Older than 30 d: dropped.

## Acceptance criteria

- ✅ 24 h chart renders &lt;1 s.
- ✅ 7 d chart renders &lt;2 s.
- ✅ Histogram of Lex prompt-token sizes is visibly bimodal/right-skewed (matches v0 data shape).
- ✅ Switching between metrics doesn't reload the page.
