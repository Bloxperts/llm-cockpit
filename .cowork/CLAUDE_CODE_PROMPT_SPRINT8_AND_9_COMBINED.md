# Claude Code prompt — Sprint 8 (UC-03) then Sprint 9 (UC-10), sequential

Paste this verbatim. Execute Sprint 8 fully (through tag + release) before starting Sprint 9.

---

## Answers to your five clarifying questions

1. **Steps 1 and 2 are fully specified below** — the truncation was on the paste side, not in the runbook. The full text is in this prompt.

2. **Aggregator cadence — 60 s / 3600 s is correct and intentional.** The spec's "hourly batch" language describes the output granularity, not the job interval. Running `MinuteAggregator` every 60 s keeps the 24 h chart live; an hourly batch would leave the most recent hour with no data. Note the deviation in the Sprint 8 PR body: *"MinuteAggregator runs every 60 s rather than hourly — keeps 24 h chart current; spec wording to be synced at sprint review."*

3. **`recharts` confirmed.** It is not currently in `package.json` — add it with `npm install recharts`. This is the conventional pick under DP-028 (stock components). No further sign-off needed.

4. **Histogram + daily-total charts are descoped for v0.3.0.** The four main charts (gpu_temp, vram, calls, latency) are the deliverable. Note in PR body: *"Prompt-token histogram and daily-completion-total bar chart descoped for v0.3.0; deferred to UX polish sprint."*

5. **`--delete-branch=false` was a typo.** Both runbooks below use `--delete-branch` (short flag, consistent with all prior sprints).

---

## SPRINT 8 — UC-03 Dashboard history charts

Execute this sprint completely (through `git tag v0.3.0`, `gh release create`, verified clean-venv smoke) before moving to Sprint 9.

---

### Repo state

`develop` HEAD is `424a7c7` (v0.2.1). Confirm:

```bash
git fetch origin && git log origin/develop --oneline -3
```

---

### Read first

1. `CLAUDE.md`
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-03-dashboard-history.md` — **Accepted**. Primary reference.
4. `src/cockpit/models.py` — `MetricsSnapshot`, `Message`, `Conversation`
5. `src/cockpit/services/metrics.py` — `GpuSampler`, `assemble_dashboard_snapshot`
6. `src/cockpit/routers/dashboard.py` — existing snapshot + stream endpoints
7. `frontend/src/app/dashboard/page.tsx` — current dashboard layout
8. `frontend/package.json` — `recharts` is NOT yet installed; add it now

---

### Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-03-dashboard-history
```

Commit prefix: `[UC-03]`. One PR against `develop`.

---

### What already exists — do not rebuild

- `MetricsSnapshot` table (5 s rows from `GpuSampler`): `ts`, `gpu_index`,
  `vram_used_mb`, `vram_total_mb`, `temp_c`, `power_w`.
- `Message` table: `conversation_id`, `role`, `model`, `usage_in`, `usage_out`,
  `gen_tps`, `latency_ms`, `ts`.
- `Conversation` table: `user_id`, `mode`, `model`.
- `GpuSampler` — already running every 5 s, data is accumulating.

---

### Step 0 — Fill in test spec

`docs/specs/test/UC-03-dashboard-history.md` is a stub. Fill in:
- Approach: unit tests for aggregation SQL + history endpoint; no chart rendering
  tests (Vitest for that is out of scope for v0.1).
- Test cases covering every AC in the functional spec.
- Pass criteria: ≥ 90 % coverage on `routers/dashboard_history.py`,
  `services/aggregator.py`.

Flip status to `Accepted`. Commit: `[UC-03] fill in test spec`.

---

### Step 1 — Database migration `0005_history.py`

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

### Step 2 — Aggregation service `src/cockpit/services/aggregator.py`

Two classes that run as background tasks (same pattern as `GpuSampler.run()`):

#### `MinuteAggregator`

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

Purge rows older than 7 days from `metrics_snapshot`:
```sql
DELETE FROM metrics_snapshot WHERE ts < datetime('now', '-7 days')
```

#### `HourAggregator`

Runs every **3600 s**. Aggregates the last completed hour of `metrics_snapshot_minute`
into `metrics_snapshot_hour`. Same shape, same idempotent upsert.

Purge `metrics_snapshot_minute` rows older than 30 days.

#### Wiring into `main.py` lifespan

Add `MinuteAggregator` and `HourAggregator` as `asyncio.Task` objects alongside
`GpuSampler` and `ModelStateSampler`. Pass `session_factory`.

Both expose `aggregate_once()` for tests.

---

### Step 3 — History endpoint `src/cockpit/routers/dashboard_history.py`

```
GET /api/dashboard/history
  ?range=24h|7d            (required)
  &metric=gpu_temp|vram|calls|latency|tokens   (required)
  &gpu=0|1|...             (optional, default all)
```

Requires `Depends(current_user_must_be_settled)`.

Response: `{ "series": [ { "label": str, "data": [ { "ts": ISO, "value": float } ] } ] }`

**`gpu_temp`** (24 h → `metrics_snapshot_minute`, 7 d → `metrics_snapshot_hour`):
```sql
SELECT bucket_ts AS ts, temp_c_avg AS value
FROM metrics_snapshot_minute
WHERE bucket_ts >= :start AND gpu_index = :gpu
ORDER BY bucket_ts
```
One series per GPU.

**`vram`** — same tables, `vram_used_mb_avg` field. One series per GPU.

**`calls`** (from `messages` table):
```sql
SELECT strftime('%Y-%m-%dT%H:%M:00', ts) AS ts, COUNT(*) AS value
FROM messages
WHERE ts >= :start AND role = 'assistant'
GROUP BY strftime('%Y-%m-%dT%H:%M:00', ts)
ORDER BY ts
```
Adjust strftime pattern to `'%Y-%m-%dT%H:00:00'` for the 7 d (hourly) variant.

**`latency`** — p50 and p95 per bucket. SQLite has no `PERCENTILE_DISC`.
Workaround: pull all `latency_ms` values per bucket, sort in Python, compute
percentiles in the service layer. Two series: "p50" + "p95".

**`tokens`** — `SUM(usage_in)` and `SUM(usage_out)` per bucket from `messages`.
Two series: "Input tokens" and "Output tokens".

Register under prefix `/api/dashboard` in `main.py`.

---

### Step 4 — Frontend: history tab on dashboard

**`frontend/src/app/dashboard/page.tsx`**

Add a tab bar at the top: `[ Live ]  [ History ]`

The existing content goes under **Live**. History tab has two sub-tabs: **24 h** and **7 d**.

Install recharts: `npm install recharts`

Use `recharts` `LineChart` / `AreaChart` / `BarChart` with `ResponsiveContainer`.
Fetch on tab open (lazy). Use `useQuery` with `staleTime: 60_000`.

Four chart cards:

1. **GPU Temperature** — `LineChart`, one line per GPU, Y-axis °C, colours matching
   status-badge thresholds (emerald ≤ 70, sky 71–82, amber 83–89, rose ≥ 90).
2. **VRAM Used** — `AreaChart` stacked per GPU, Y-axis MB.
3. **Request rate** — `BarChart`, Y-axis calls/min (calls/h for 7 d).
4. **Latency p50 / p95** — `LineChart` two lines, Y-axis ms. Hidden if no data.

Layout: 2-column grid on wide screens, single column on narrow. Each card:
`bg-white dark:bg-neutral-900 rounded-xl border border-neutral-200 dark:border-neutral-800 p-4`

Tooltip timestamps: `Intl.DateTimeFormat` → `HH:mm` (24 h) / `MMM d HH:mm` (7 d).

Loading state: animated neutral skeleton pulse.
Empty state: "No data yet — GPU metrics accumulate over time."

---

### Step 5 — Spec status edits

`docs/specs/functional/UC-03-dashboard-history.md`: `Accepted → In Progress` at
branch open, `Done (technical)` when tests pass.

---

### Coverage target (Sprint 8)

```bash
pytest --cov=cockpit.routers.dashboard_history \
       --cov=cockpit.services.aggregator \
       --cov-report=term-missing
```

≥ 90 % on both modules.

---

### Build + release (Sprint 8)

Update `pyproject.toml` version to `0.3.0` before building.

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
- Latency p95 computed in Python (SQLite has no PERCENTILE_DISC)
- NOTE: MinuteAggregator runs every 60 s rather than hourly per spec — keeps 24 h chart current; spec to be synced at sprint review
- NOTE: Prompt-token histogram + daily-total bar chart descoped for v0.3.0; deferred to UX polish sprint"

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
- VRAM usage stacked area chart per GPU
- Request rate bar chart (calls/min or calls/h)
- Latency p50/p95 line chart
- Background aggregation: 5 s raw → 1-min buckets → 1-h buckets; automatic data pruning"
```

**Verify the release is clean before continuing to Sprint 9:**

```bash
cockpit-admin --version   # must print 0.3.0
git log origin/develop --oneline -3
```

---

---

## SPRINT 9 — UC-10 Admin Ollama configuration

Only start after Sprint 8 is fully merged, tagged, and released as v0.3.0.

---

### Repo state

`develop` HEAD is the v0.3.0 merge. Confirm:

```bash
git fetch origin && git log origin/develop --oneline -3
```

---

### Read first

1. `CLAUDE.md`
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-10-ollama-configuration.md` — **Accepted**. Primary reference.
4. `src/cockpit/routers/admin_ollama.py` — what already exists.
5. `src/cockpit/services/model_tags.py` — existing tag heuristic logic.
6. `src/cockpit/models.py` — `ModelTag`, `Settings`, `ModelPerf`, `Message`.
7. `src/cockpit/routers/admin_users.py` — pattern to follow for the audit endpoint.

---

### Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-10-admin-ollama-config
```

Commit prefix: `[UC-10]`. One PR against `develop`.

---

### What already exists — do not rebuild

- `POST /api/admin/ollama/models/{model}/pull` — SSE pull progress ✓
- `DELETE /api/admin/ollama/models/{model}` — delete model ✓
- `POST /api/admin/ollama/models/{model}/place` — placement transition ✓
- `POST /api/admin/ollama/models/{model}/perf-test` — perf harness ✓
- `PATCH /api/admin/ollama/models/{model}/settings` — per-model keep_alive/ctx/notes ✓
- `ModelTag` table + `model_tags.py` service (heuristic resolution) ✓
- `Settings` table ✓
- `AdminAudit` table + `write_admin_audit()` ✓
- `Message` table with `model`, `usage_in`, `usage_out`, `latency_ms`, `gen_tps` ✓

---

### Step 0 — Fill in test spec

`docs/specs/test/UC-10-ollama-configuration.md` is a stub. Fill in approach +
test cases covering every AC. Flip to `Accepted`. Commit: `[UC-10] fill in test spec`.

---

### Step 1 — New / missing backend endpoints

All routes in `src/cockpit/routers/admin_ollama.py` unless noted.
All require `Depends(require_role("admin"))`.

#### 1a — Model tag management

**`PATCH /api/admin/ollama/models/{model}/tag`**

Body: `{ "tag": "chat" | "code" | "both" }`

- Upsert `ModelTag(model=model, tag=body.tag, source='override')`.
- Write `AdminAudit(action='model_tag_set', target_model=model, details={'tag': body.tag})`.
- Re-evaluate heuristics for all models.
- Return `{ "model": model, "tag": body.tag, "source": "override" }`.

**`DELETE /api/admin/ollama/models/{model}/tag`**

- Delete the `ModelTag` row (removes the override).
- Re-apply heuristic: insert a new row with `source='auto'`.
- Write audit `action='model_tag_cleared'`.
- Return 204.

#### 1b — Settings GET/PUT

**`GET /api/admin/ollama/settings`**

- Return `{ "code_default_system_prompt": str | null, "tag_heuristics_yaml": str | null }`.
- Read from `Settings` table by key. Return `null` if key doesn't exist.
- Before adding a second writer: grep for existing writes to `code_default_system_prompt`
  in the codebase (DP-013 single-writer hygiene). If found, route through the same
  path rather than adding a parallel writer.

**`PUT /api/admin/ollama/settings`**

Body: `{ "code_default_system_prompt"?: str, "tag_heuristics_yaml"?: str }`

- For each provided key: upsert the `Settings` row.
- If `tag_heuristics_yaml` updated: re-evaluate all model tags immediately using
  `app.state.model_state.available_models` (already cached).
- Write `AdminAudit(action='settings_updated', details={keys_changed})`.
- Return `{ "updated": [list of keys changed] }`.

#### 1c — Per-model metrics endpoints

**`GET /api/admin/ollama/metrics`**

```sql
SELECT m.model,
       COUNT(*) AS calls,
       COALESCE(SUM(m.usage_in), 0) AS prompt_tokens,
       COALESCE(SUM(m.usage_out), 0) AS completion_tokens,
       AVG(m.latency_ms) AS mean_latency_ms,
       AVG(m.gen_tps) AS mean_gen_tps,
       MAX(m.ts) AS last_call_at
FROM messages m
WHERE m.ts >= datetime('now', '-7 days')
  AND m.role = 'assistant'
  AND m.model IS NOT NULL
GROUP BY m.model
ORDER BY calls DESC
```

Omit p95 here (too expensive for list view). Response: `list[ModelMetricsSummary]`.

**`GET /api/admin/ollama/metrics/{model}`**

```sql
SELECT role, usage_in, usage_out, latency_ms, gen_tps, ts, error
FROM messages
WHERE model = :model AND role = 'assistant'
ORDER BY ts DESC
LIMIT 50
```

Compute p95 latency in Python from the returned rows.
Response: `{ "calls": [...], "p95_latency_ms": float | null }`.

#### 1d — Audit log endpoint

**`GET /api/admin/audit`**

Merges `login_audit` and `admin_audit`. Query params: `?page=1&per_page=50&action=&username=`

Implement as two separate queries merged and sorted in Python (simpler than UNION
across heterogeneous schemas in SQLite).

```python
class AuditEntry(BaseModel):
    source: str        # "login" | "admin"
    ts: datetime
    actor: str | None
    action: str
    target: str | None
    details: dict | None
    source_ip: str | None

class AuditResponse(BaseModel):
    entries: list[AuditEntry]
    total: int
    page: int
    per_page: int
```

**`GET /api/admin/audit/export`** — same filters, no pagination, returns
`text/csv` with `Content-Disposition: attachment; filename=audit.csv`.

Register under new `/api/admin/audit` prefix in `main.py`.

---

### Step 2 — Heuristic re-evaluation on model tag save

In `src/cockpit/services/model_tags.py`, ensure:

```python
def reapply_heuristics(
    session: Session,
    available_models: list[ModelInfo],
    yaml_override: str | None = None,
) -> None:
    """Re-evaluate tag heuristics for all models. Override rows are untouched.
    Auto rows are updated. Called after settings save or new model detection."""
```

Call from `PUT /api/admin/ollama/settings` when `tag_heuristics_yaml` changes,
and wire into `ModelStateSampler.sample_once()` for new model detection.

---

### Step 3 — Frontend: `/admin/ollama` page

Create `frontend/src/app/admin/ollama/page.tsx`. Gate: `role === 'admin'` only.

Add **"Ollama"** link in `AppHeader.tsx` (admin-only, next to "Users").

**Four collapsible panels** (`<details>/<summary>` — no new UI library):

**Panel 1 — Model tags**
Table: `Model | Size | Tag | Source | Actions`
- Data: `GET /api/dashboard/snapshot` (already cached, has model list + tags).
- Tag column: `<select>` chat/code/both → `PATCH .../tag` on change.
- Source badge: `auto` (neutral) / `override` (amber).
- "Clear override" button (only when `source === 'override'`) → `DELETE .../tag`.
- Pull new model: text input + "Pull" button → `<dialog>` streaming SSE from `POST .../pull`.
- Delete button per row: confirmation → `DELETE` endpoint.

**Panel 2 — Defaults**
- Code system prompt: `<textarea rows=8>` bound to `code_default_system_prompt`.
- Tag heuristics YAML: monospace `<textarea rows=12>` bound to `tag_heuristics_yaml`.
- "Save" → `PUT /api/admin/ollama/settings`. Success/error flash.

**Panel 3 — Per-model metrics (last 7 days)**
Sortable table: `Model | Calls | Prompt tokens | Completion tokens | Avg latency | Avg TPS | Last call`
- Click row → `<dialog>` with last 50 calls table + p95 latency.

**Panel 4 — Audit log**
Paginated table: `Time | Source | Actor | Action | Target | IP`
- Filters: Action + Username text inputs.
- "Export CSV" → navigates to `/api/admin/audit/export` (browser handles download).
- Prev / Next pagination.

---

### Step 4 — Spec status edits

`docs/specs/functional/UC-10-ollama-configuration.md`: `Accepted → In Progress`,
then `Done (technical)` when tests pass.

---

### Coverage target (Sprint 9)

```bash
pytest --cov=cockpit.routers.admin_ollama \
       --cov=cockpit.services.model_tags \
       --cov-report=term-missing
```

≥ 90 % on both modules. All Sprint 8 tests must stay green.

---

### Build + release (Sprint 9)

Update `pyproject.toml` version to `0.3.1` before building.

```bash
make build

gh pr create \
  --base develop \
  --head feature/UC-10-admin-ollama-config \
  --title "[UC-10] Admin Ollama configuration page" \
  --body "UC-10 admin Ollama config:
- PATCH/DELETE /api/admin/ollama/models/{model}/tag — override and clear model tags
- GET/PUT /api/admin/ollama/settings — code system prompt + tag heuristics YAML editor
- GET /api/admin/ollama/metrics + /{model} — per-model 7-day rollup + last 50 calls drill-down
- GET /api/admin/audit + /export — merged login+admin audit log, paginated, CSV export
- /admin/ollama frontend page: 4 panels (model tags, defaults editor, metrics, audit log)
- Heuristic re-evaluation on settings save and new model detection"

gh pr merge --squash \
  --subject "[UC-10] Admin Ollama configuration page" \
  --delete-branch

git checkout develop && git pull
git tag v0.3.1
git push origin v0.3.1
gh release create v0.3.1 dist/llm_cockpit-0.3.1-py3-none-any.whl \
  --title "v0.3.1 — Admin Ollama configuration" \
  --notes "- /admin/ollama page with four panels
- Model tag management: override chat/code/both per model, clear to revert to heuristic
- Settings editor: code system prompt + tag heuristics YAML (takes effect immediately)
- Per-model metrics: 7-day rollup table + last 50 calls drill-down with p95 latency
- Audit log: merged login + admin events, filterable, CSV export"
```

---

## Stop and ask Chris if (either sprint)

- Sprint 8: `metrics_snapshot` has fewer than ~300 rows on Neuroforge — charts will
  show "No data yet" until data accumulates; that's expected behaviour, not a bug.
- Sprint 9: existing code already writes to `code_default_system_prompt` via a
  different path — grep first, route through one writer.
- Sprint 9: UNION query for audit log is slow — fall back to two queries + Python merge.
