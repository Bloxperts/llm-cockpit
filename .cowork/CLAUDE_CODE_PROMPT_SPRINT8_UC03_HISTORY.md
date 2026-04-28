# Claude Code prompt — Sprint 8: UC-03 Dashboard history charts

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

`develop` HEAD is `424a7c7` (v0.2.1). Confirm:

```bash
git fetch origin && git log origin/develop --oneline -3
```

---

## Read first

1. `CLAUDE.md`
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-03-dashboard-history.md` — **Accepted**. Primary reference.
4. `src/cockpit/models.py` — `MetricsSnapshot`, `Message`, `Conversation`
5. `src/cockpit/services/metrics.py` — `GpuSampler`, `assemble_dashboard_snapshot`
6. `src/cockpit/routers/dashboard.py` — existing snapshot + stream endpoints
7. `frontend/src/app/dashboard/page.tsx` — current dashboard layout
8. `frontend/package.json` — confirm `recharts` is already installed

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-03-dashboard-history
```

Commit prefix: `[UC-03]`. One PR against `develop`.

---

## What already exists — do not rebuild

- `MetricsSnapshot` table (5 s rows from `GpuSampler`): `ts`, `gpu_index`,
  `vram_used_mb`, `vram_total_mb`, `temp_c`, `power_w`.
- `Message` table: `conversation_id`, `role`, `model`, `usage_in`, `usage_out`,
  `gen_tps`, `latency_ms`, `ts`.
- `Conversation` table: `user_id`, `mode`, `model`.
- `GpuSampler` — already running every 5 s, data is accumulating.
- `recharts` — already in `frontend/package.json`.

---

## Step 0 — Fill in test spec

`docs/specs/test/UC-03-dashboard-history.md` is a stub. Fill in:
- Approach: unit tests for aggregation SQL + history endpoint; no chart rendering
  tests (Vitest for that is out of scope for v0.1).
- Test cases covering every AC in the functional spec.
- Pass criteria: ≥ 90 % coverage on `routers/dashboard_history.py`,
  `services/aggregator.py`.

Flip status to `Accepted`. Commit: `[UC-03] fill in test spec`.

---

## Step 1 — Database migration `0005_history.py`

Two new tables:

```python
class MetricsSnapshotMinute(Base):
    __tablename__ = "metrics_snapshot_minute"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb_avg: Mapped[float] = mapped_column(Float, nullable=False)
    temp_c_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        Index("idx_msm_gpu_ts", "gpu_index", "bucket_ts"),
        UniqueConstraint("bucket_ts", "gpu_index", name="uq_msm_bucket_gpu"),
    )

class MetricsSnapshotHour(Base):
    __tablename__ = "metrics_snapshot_hour"
    # Same shape as minute table, different granularity
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb_avg: Mapped[float] = mapped_column(Float, nullable=False)
    temp_c_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        Index("idx_msh_gpu_ts", "gpu_index", "bucket_ts"),
        UniqueConstraint("bucket_ts", "gpu_index", name="uq_msh_bucket_gpu"),
    )
```

Also add an index on `metrics_snapshot.ts` if not already present (check models.py).
Add index on `messages.ts` for the call-rate queries.

---

## Step 2 — Aggregation service `src/cockpit/services/aggregator.py`

Two functions that run as background tasks (similar to `GpuSampler.run()`):

### `MinuteAggregator`

Runs every **60 s**. Aggregates the last completed minute of `metrics_snapshot` rows
into `metrics_snapshot_minute`:

```python
bucket_ts = floor_to_minute(now - timedelta(minutes=1))
```

Query (per gpu_index):
```sql
SELECT gpu_index,
       AVG(vram_used_mb), AVG(temp_c), MAX(temp_c), AVG(power_w),
       COUNT(*)
FROM metrics_snapshot
WHERE ts >= :bucket_start AND ts < :bucket_end
GROUP BY gpu_index
```

Insert with `INSERT OR IGNORE` (idempotent — safe to re-run).

Purge rows older than 7 days from `metrics_snapshot` (keep raw data for 7 d only):
```sql
DELETE FROM metrics_snapshot WHERE ts < datetime('now', '-7 days')
```

### `HourAggregator`

Runs every **3600 s**. Aggregates the last completed hour of `metrics_snapshot_minute`
into `metrics_snapshot_hour`. Same shape, same idempotent upsert.

Purge `metrics_snapshot_minute` rows older than 30 days.

### Wiring into `main.py` lifespan

Add `MinuteAggregator` and `HourAggregator` as `asyncio.Task` objects alongside
`GpuSampler` and `ModelStateSampler`. Pass `session_factory`.

Both expose `aggregate_once()` for tests.

---

## Step 3 — History endpoint `src/cockpit/routers/dashboard_history.py`

```
GET /api/dashboard/history
  ?range=24h|7d            (required)
  &metric=gpu_temp|vram|calls|latency|tokens   (required)
  &gpu=0|1|...             (optional, default all)
```

Requires `Depends(current_user_must_be_settled)`.

Response shape: `{ "series": [ { "label": str, "data": [ { "ts": ISO, "value": float } ] } ] }`

### Metric implementations

**`gpu_temp`** (24 h → `metrics_snapshot_minute`, 7 d → `metrics_snapshot_hour`):
```sql
SELECT bucket_ts AS ts, temp_c_avg AS value
FROM metrics_snapshot_minute   -- or _hour
WHERE bucket_ts >= :start AND gpu_index = :gpu
ORDER BY bucket_ts
```
One series per GPU.

**`vram`** — same tables, `vram_used_mb_avg` field. One series per GPU.

**`calls`** (calls per minute / per hour from `messages` table):
```sql
SELECT strftime('%Y-%m-%dT%H:%M:00', ts) AS ts, COUNT(*) AS value
FROM messages
WHERE ts >= :start AND role = 'assistant'
GROUP BY strftime('%Y-%m-%dT%H:%M:00', ts)  -- 1-min buckets for 24h
ORDER BY ts
```
Adjust strftime for 1-h buckets on 7 d. One series: "All models". Optionally break
out per model if `?model=` param present (nice-to-have, not required for v0.1).

**`latency`** — p50 and p95 per bucket. SQLite has no native PERCENTILE_DISC.
Workaround: pull all `latency_ms` values per bucket (small set per minute), sort
in Python, compute p50/p95 in the service layer. For 24 h this is at most
60 × 5 = 300 values per minute-bucket — fine in memory. Two series: "p50" + "p95".

**`tokens`** — sum of `usage_in` + `usage_out` per bucket from `messages`. One
series each: "Input tokens" and "Output tokens".

Register under prefix `/api/dashboard` in `main.py`.

---

## Step 4 — Frontend: history tab on dashboard

**`frontend/src/app/dashboard/page.tsx`**

Add a tab bar at the top of the dashboard:

```
[ Live ]  [ History ]
```

The existing content goes under **Live**. The new **History** tab renders the charts.

Within History, add two sub-tabs: **24 h** and **7 d**.

### Charts (use recharts)

Use `recharts` `LineChart` / `AreaChart` / `BarChart` with `ResponsiveContainer`.
Fetch from `/api/dashboard/history?range=24h&metric=...` on tab open (lazy load).
Use `useQuery` (TanStack Query already wired) with `staleTime: 60_000`.

Four chart cards, each with a title and the chart:

1. **GPU Temperature** — `LineChart` one line per GPU, Y-axis °C, colour-coded to
   match the status-badge colours (emerald/sky/amber/rose by threshold).
2. **VRAM Used** — `AreaChart` stacked per GPU, Y-axis MB.
3. **Request rate** — `BarChart`, Y-axis calls/min (calls/h for 7 d).
4. **Latency p50 / p95** — `LineChart` two lines, Y-axis ms. Only shown if data
   exists (messages table has rows).

Layout: 2-column grid on wide screens, single column on narrow. Each card:
`bg-white dark:bg-neutral-900 rounded-xl border border-neutral-200 dark:border-neutral-800 p-4`.

Tooltip format: ISO timestamp → formatted as `HH:mm` (24 h) or `MMM d HH:mm` (7 d)
using `Intl.DateTimeFormat`. No external date library needed.

Loading state: skeleton placeholder (neutral animated pulse) while fetch is in flight.
Empty state: "No data yet — GPU metrics accumulate over time." when series is empty.

---

## Step 5 — Spec status edits

- `docs/specs/functional/UC-03-dashboard-history.md`: `Accepted → In Progress`
  at branch open, `Done (technical)` when tests pass.

---

## Coverage target

```bash
pytest --cov=cockpit.routers.dashboard_history \
       --cov=cockpit.services.aggregator \
       --cov-report=term-missing
```

≥ 90 % on both modules.

---

## Build + release

```bash
make build

gh pr create \
  --base develop \
  --head feature/UC-03-dashboard-history \
  --title "[UC-03] Dashboard history charts" \
  --body "UC-03 dashboard history tab:
- Migration 0005: metrics_snapshot_minute + metrics_snapshot_hour tables
- MinuteAggregator (60 s) + HourAggregator (3600 s) background tasks; raw data pruned after 7 d / 30 d
- GET /api/dashboard/history?range=24h|7d&metric=gpu_temp|vram|calls|latency|tokens
- Dashboard History tab with 24h/7d sub-tabs; 4 recharts cards: GPU temp, VRAM, request rate, latency p50/p95
- Latency p95 computed in Python (SQLite has no PERCENTILE_DISC)"

gh pr merge --squash \
  --subject "[UC-03] Dashboard history charts" \
  --delete-branch

git checkout develop && git pull
git tag v0.3.0
git push origin v0.3.0
gh release create v0.3.0 dist/llm_cockpit-0.3.0-py3-none-any.whl \
  --title "v0.3.0 — Dashboard history charts" \
  --notes "- New History tab on dashboard with 24 h and 7 d views
- GPU temperature line chart (colour-coded to status thresholds)
- VRAM usage area chart per GPU
- Request rate bar chart (calls/min or calls/h)
- Latency p50/p95 line chart
- Background aggregation: 5 s raw data → 1-min buckets → 1-h buckets; automatic pruning"
```

Note: minor version bump to `v0.3.0` — new user-visible feature tab. Update
`pyproject.toml` version to `0.3.0` before building.

---

## Stop and ask Chris if

- The `metrics_snapshot` table has fewer than ~300 rows (less than 25 min of data)
  — the aggregator will produce empty buckets which is fine, but the chart will show
  "No data yet" until enough accumulates.
- `recharts` version in `package.json` differs from what's expected — check types
  are compatible before importing.
- SQLite's `strftime` format strings produce a different bucket alignment than
  expected — test with a small sample query before wiring into the endpoint.
