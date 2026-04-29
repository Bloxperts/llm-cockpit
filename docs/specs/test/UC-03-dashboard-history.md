<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-03-dashboard-history · Test Spec — Dashboard history

**Status:** Accepted
**Owner:** Chris
**User Spec:** [`user/UC-03-dashboard-history.md`](../../use-cases/UC-03-dashboard-history.md)
**Functional Spec:** [`functional/UC-03-dashboard-history.md`](../functional/UC-03-dashboard-history.md)

## Approach

UC-03 is a pure read-shape story over already-populated tables (`metrics_snapshot`,
`messages`) plus two new aggregation tables (`metrics_snapshot_minute`,
`metrics_snapshot_hour`). All boundary-crossing surfaces — Ollama, GPU
telemetry — are upstream of UC-03; the new code reads from SQLite only and
runs two periodic background tasks. That keeps the test surface entirely
in-process.

Three test layers:

1. **Migration** — `0005_history.py` creates the two new tables, plus
   indexes on `metrics_snapshot.ts` and `messages.ts` if not already
   present. Verified by an `alembic upgrade head` round-trip in a fresh
   SQLite DB and assertion-style row inserts.

2. **Aggregator service** (`services/aggregator.py`) — each of
   `MinuteAggregator` and `HourAggregator` exposes an `aggregate_once()`
   method invoked directly in tests. Tests insert known
   `metrics_snapshot` / `metrics_snapshot_minute` rows, call
   `aggregate_once()`, and assert (a) the produced bucket rows match the
   expected averages / max / counts, (b) the upsert is idempotent
   (running twice produces the same shape), and (c) the pruning step
   deletes rows older than the retention window. No real `asyncio.run()`
   timing — tests don't wait wall-clock seconds.

3. **History endpoint** (`routers/dashboard_history.py`) — `TestClient`
   round-trips against an in-memory SQLite. Tests cover every
   `metric` × `range` combination plus the auth gate, the empty-data
   shape, the latency p50/p95 percentile computation, and the calls /
   tokens GROUP BY bucket alignment.

No chart-rendering tests in this sprint — Vitest for the frontend is
explicitly out of v0.1 scope per `process/PROCESS.md`. Visual smoke is
the sprint-review check.

## Test cases

### Migration 0005 (1 test)

- **`test_migration_0005_round_trip`** — `alembic upgrade head` against a
  fresh SQLite engine, then `downgrade -1` then `upgrade head` again;
  asserts both new tables exist after upgrade, are gone after downgrade,
  and that an `INSERT … ON CONFLICT (bucket_ts, gpu_index) DO NOTHING`
  succeeds idempotently.

### Aggregator service (8 tests)

- **`test_minute_aggregator_buckets_correct_average`** — insert N
  snapshots within one wall-minute for two GPUs, call
  `MinuteAggregator.aggregate_once()`, assert the row in
  `metrics_snapshot_minute` has the right `vram_used_mb_avg`,
  `temp_c_avg`, `temp_c_max`, `power_w_avg`, and `sample_count`.
- **`test_minute_aggregator_idempotent`** — running `aggregate_once()`
  twice in a row produces the same single row per `(bucket_ts,
  gpu_index)` (the `INSERT OR IGNORE` semantics).
- **`test_minute_aggregator_skips_partial_minute`** — only the *last
  completed* minute is bucketed; the in-progress minute is left alone
  (so it can be re-bucketed on the next tick once it closes).
- **`test_minute_aggregator_handles_empty_window`** — no
  `metrics_snapshot` rows in the window → no bucket inserted, no error
  raised.
- **`test_minute_aggregator_prunes_old_raw`** — rows in
  `metrics_snapshot` older than 7 d are removed; younger rows are
  preserved.
- **`test_hour_aggregator_buckets_from_minute_table`** — insert
  `metrics_snapshot_minute` rows spanning one hour; assert
  `metrics_snapshot_hour` carries the correct hourly average and max.
- **`test_hour_aggregator_prunes_old_minute`** — `metrics_snapshot_minute`
  rows older than 30 d are removed.
- **`test_aggregator_swallows_db_errors`** — a forced exception inside
  `aggregate_once()` is logged and does not propagate; the next call
  proceeds.

### History endpoint (12 tests)

For each metric × range combination unless noted:

- **`test_history_requires_auth`** — `401` when no cookie is present.
- **`test_history_rejects_invalid_range`** — `range=99h` → `422`.
- **`test_history_rejects_invalid_metric`** — `metric=foo` → `422`.
- **`test_history_gpu_temp_24h_returns_per_gpu_series`** — two GPUs in
  `metrics_snapshot_minute` → two series in the response, ISO timestamps,
  ordered by `bucket_ts`.
- **`test_history_gpu_temp_7d_uses_hour_table`** — `range=7d` reads from
  `metrics_snapshot_hour` not `metrics_snapshot_minute`.
- **`test_history_vram_24h_returns_per_gpu_series`** — same shape as
  gpu_temp but `vram_used_mb_avg` values.
- **`test_history_calls_24h_minute_buckets`** — 5 assistant messages
  spread across 3 minutes → 3 rows with the right counts.
- **`test_history_calls_7d_hour_buckets`** — strftime pattern aggregates
  into hourly buckets.
- **`test_history_calls_excludes_user_and_system_rows`** — only
  `role='assistant'` is counted.
- **`test_history_latency_computes_p50_and_p95`** — known latency_ms
  values produce the right percentiles per bucket; two series labelled
  `p50` and `p95`.
- **`test_history_latency_skips_null_rows`** — rows with `latency_ms IS
  NULL` are excluded from the percentile computation.
- **`test_history_tokens_returns_input_and_output_series`** — sum of
  `usage_in` and `usage_out` per bucket; two series.

### Empty data (2 tests)

- **`test_history_empty_metrics_returns_empty_series_list`** — DB has
  zero rows → `{"series": []}` not a 500.
- **`test_history_partial_data_does_not_break_chart_shape`** — only one
  GPU has temp readings → one series, not two.

## Pass criteria

- All automated tests pass on `develop` and `main`.
- Manual smoke: open `/dashboard`, switch to **History** tab, see all four
  charts render under both `24 h` and `7 d` sub-tabs (or render the empty
  state if telemetry hasn't accumulated yet).
- Coverage ≥ 90 % on `routers/dashboard_history.py` and
  `services/aggregator.py`.
- Backwards-compatible with v0.2.1 — no existing tests regress.
