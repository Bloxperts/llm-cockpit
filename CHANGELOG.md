# Changelog

All notable changes to **llm-cockpit** are documented here. The project
follows SemVer once it reaches v0.1.0; pre-release alphas use `v0.X.Yaβ`.

## [v0.5.0] — 2026-04-30 — UI refresh and interaction polish (UC-12)

Minor-version bump for Sprint 11. UC-12 starts the release-quality UI pass
before the public PyPI `v1.0.0` sprint.

### Added — Frontend

- Introduced shared cockpit UI tokens and reusable utility classes for
  panels, buttons, inputs, tables, focus states, and page spacing.
- Reworked the app header into a steadier shell with responsive navigation,
  theme toggle, and account/session controls.
- Reworked the dashboard live view with GPU cards, placement columns,
  polished model cards, pending/error placement feedback, and a visually
  integrated perf-test drawer.
- Restored drag-and-drop model placement on the dashboard using the existing
  `@dnd-kit/core` dependency while preserving the per-card select fallback for
  keyboard and touch users.
- Brought chat, code, admin users, admin Ollama, login, change-password, and
  dashboard history surfaces onto the same visual system.

### Tests

- Frontend TypeScript check passes with `npx tsc --noEmit`.
- Frontend production build passes with `npm run build`.

## [v0.4.0] — 2026-04-29 — Perf-test progress UI (UC-02 v1.1)

Minor-version bump for the UC-02 v1.1 amendment. The dashboard perf-test
action now behaves like a live operation instead of a silent long-running
request: admins see stages, elapsed time, throughput progress, cancellation,
and stalled-network detection.

### Added — Backend

- **Perf-test SSE protocol v1.1** on
  `POST /api/admin/ollama/models/{model}/perf-test`:
  - `stage` events include `{ name, started_at }`.
  - `progress` events include `{ stage, elapsed_ms }`; throughput progress
    also includes `tokens_so_far` and `tokens_per_sec`.
  - `heartbeat` events emit every second while a stage is active if no other
    event has gone out.
  - Terminal events are now explicit: `result`, `cancelled`, or `error`.
- **Cooperative cancellation**:
  - `POST /api/admin/ollama/models/{model}/perf-test/cancel` flips the
    active run's `asyncio.Event`.
  - The harness checks cancellation between stages and while consuming
    `chat_stream`.
  - Cancelled runs restore prior placement, release the model lock, write
    `admin_audit(action='model_perf_test_cancel')`, and deliberately skip
    `model_perf` persistence.

### Added — Frontend

- Replaced the raw perf-test log modal on `/dashboard` with a progress drawer:
  stage badge, elapsed timer, live tokens/s during throughput, Cancel button,
  terminal result card, and cancelled/error states.
- The drawer shows a stalled warning when no SSE event arrives for 2 seconds,
  while keeping Cancel available.
- The bundled static frontend was rebuilt into `src/cockpit/frontend_dist/`.

### Tests

- UC-02 backend tests now cover the v1.1 result shape, progress events,
  heartbeat helper, cancel route, cancelled terminal event, audit row, and
  the no-`model_perf` guarantee for cancelled runs.

## [v0.3.1] — 2026-04-28 — Admin Ollama configuration page (UC-10)

Patch on top of v0.3.0. UC-10 ships the `/admin/ollama` admin surface:
the four-panel page that holds the controls that don't belong on the
placement board — model tag overrides, the code-mode default system
prompt, the tag-heuristics YAML editor, per-model metrics, and the
unified login + admin audit log. UC-10 functional spec moves Accepted
→ In Progress → Done (technical) on this release.

### Added — Backend

- **Tag CRUD** (`routers/admin_ollama.py`):
  - `PATCH /api/admin/ollama/models/{model}/tag` — body
    `{ tag: 'chat' | 'code' | 'both' }`. Upserts the row with
    `source='override'`. Returns `{ model, tag, source: 'override' }`.
    Audit `model_tag_set`. Tag validation rejects anything outside the
    three values with `422`.
  - `DELETE /api/admin/ollama/models/{model}/tag` — removes the
    override and re-applies the heuristic over the cached model list
    so the row gets back an `auto` tag in the same transaction.
    Idempotent (`204` even when no override existed) — no audit row
    when there was nothing to clear. Audit `model_tag_cleared`
    otherwise.
- **Settings GET / PUT**:
  - `GET /api/admin/ollama/settings` — returns
    `{ code_default_system_prompt, tag_heuristics_yaml }` with `null`
    for any unset key.
  - `PUT /api/admin/ollama/settings` — partial-PUT. Only keys
    explicitly supplied in the body are written. A malformed
    `tag_heuristics_yaml` returns `400 invalid_yaml` **before** the DB
    write, so a bad YAML never displaces a good one. When
    `tag_heuristics_yaml` changes, the new patterns drive
    `reapply_heuristics()` over the cached model list in the same
    transaction so auto-tagged rows update immediately. Audit
    `settings_updated` lists the changed keys.
  - DP-013 single-writer hygiene confirmed: the PUT endpoint is the
    first writer for both `code_default_system_prompt` and
    `tag_heuristics_yaml`.
- **Per-model metrics** (last 7 d, assistant rows only):
  - `GET /api/admin/ollama/metrics` — rollup table:
    `model · calls · prompt_tokens · completion_tokens · mean_latency_ms ·
    mean_gen_tps · last_call_at`, ordered by `calls DESC`. p95 omitted
    by design (would need to pull all rows per model).
  - `GET /api/admin/ollama/metrics/{model}` — last 50 calls + Python-
    computed p95 latency.
- **Unified audit log** (`routers/admin_audit.py`, new module):
  - `GET /api/admin/audit?page=&per_page=&action=&username=` — merges
    `login_audit` + `admin_audit` into a single time-sorted feed.
    Two SELECTs merged in Python (UNION across heterogeneous schemas
    in SQLite is awkward; v0.1's tables are tiny — even a heavily-used
    cockpit accumulates a few hundred rows a day).
  - `GET /api/admin/audit/export` — same filters, no pagination,
    `Content-Type: text/csv` with `Content-Disposition: attachment;
    filename=audit.csv`.

### Added — Heuristic re-evaluation

- `services/model_tags.py` adds `reapply_heuristics(session,
  available_models, yaml_override=None)`. Walks the cached
  available-model list, recomputes the auto tag for every row whose
  `source='auto'`, inserts a fresh `auto` row for any name without one,
  and **never touches override rows**. Caller commits.
- `services/metrics.ModelStateSampler` grows an optional
  `session_factory`. When a never-before-seen model name first appears
  in `LLMChat.list_models()`, the sampler triggers
  `reapply_heuristics` so new models get a tag row inserted
  automatically.

### Added — Frontend

- **`/admin/ollama` page** with four collapsible panels (native
  `<details>` / `<summary>` — no new UI library):
  1. **Model tags** — table of every served model with size, current
     tag, source badge (`auto` neutral / `override` amber), inline tag
     `<select>`, "Clear override" button (only when source is
     `override`), "Delete" button per row. Plus a "Pull a model" inline
     control with SSE-streamed progress.
  2. **Defaults** — `<textarea>` for `code_default_system_prompt`,
     monospace `<textarea>` for `tag_heuristics_yaml`, "Save" button
     with success / error flash.
  3. **Per-model metrics (last 7 days)** — sortable table; click a row
     to open a `<dialog>` with the last 50 calls and the p95 latency.
  4. **Audit log** — paginated table with Action / Username text
     filters, "Apply" + "Export CSV" buttons, Prev / Next pagination.
- **`AppHeader` "Ollama" link** — admin-only, alongside the existing
  "Users" link.

### Tests

- **`tests/test_sprint9_admin_ollama.py`** (37 tests):
  - 8 auth-gate cases (every UC-10 endpoint × chat/code/unauth).
  - 8 tag-CRUD cases (override creation, audit shape, idempotent
    duplicate PATCH, invalid tag, DELETE reapplies heuristic, audit on
    DELETE, idempotent DELETE on missing override, DELETE on unknown
    model).
  - 6 settings cases (nulls, both keys, partial body, invalid YAML
    preserves prior good row, audit, YAML-change reapplies heuristic
    while overrides survive).
  - 5 metrics cases (7-day window, role filter, ordering, drill-down
    limit of 50, p95 computation).
  - 5 audit cases (merge of login+admin sources, action filter,
    username filter, pagination, CSV export shape).
  - 3 `reapply_heuristics()` helper cases (auto-row update, override
    survival, `yaml_override` arg precedence).
- **431 tests collected, all green.** Coverage on touched modules:
  `routers/admin_ollama.py` 93 %, `routers/admin_audit.py` 91 %,
  `services/model_tags.py` 94 %.

## [v0.3.0] — 2026-04-28 — Dashboard history charts (UC-03)

Minor-version bump (UC-03): the dashboard gains a second top-level tab
showing 24 h and 7 d time-series for GPU temperature, VRAM usage,
request rate, and latency. Functional spec moves from Accepted → In
Progress → Done (technical) on this release.

### Added — Dashboard history (UC-03)

- **`/dashboard` — Live / History tab bar.** The existing live view
  becomes the **Live** tab; **History** is the new tab. Live SSE keeps
  running in the background regardless of which tab is shown, so
  switching back to Live is instant. Inside History a sub-tab toggle
  flips between **24 h** and **1-min buckets** and **7 d** with
  **1-h buckets**.
- **Four chart cards** powered by recharts (added as a top-level
  frontend dep — `recharts ^3.8.1`):
  - **GPU Temperature** — `LineChart`, one line per GPU index
    (emerald, sky, amber, rose palette matching the live status badges).
    Y-axis pinned to 0–100 °C.
  - **VRAM Used** — `AreaChart` stacked per GPU index, MB units.
  - **Request rate** — `BarChart`, "calls / minute" (24 h) or
    "calls / hour" (7 d). Counts assistant `messages` rows only — user
    and system rows are excluded.
  - **Latency p50 / p95** — `LineChart` two lines, ms units. SQLite has
    no `PERCENTILE_DISC`, so latency rows are pulled per-bucket and the
    percentiles are computed in Python via linear interpolation.
- **Per-card lazy fetch** via TanStack Query (`staleTime: 60 000`).
  Switching range or remounting the History tab re-fetches; no
  page-wide poll. Each card has its own loading skeleton (animated
  pulse) and empty state ("No data yet — GPU metrics accumulate over
  time.").
- **Tooltip timestamp formatting** uses `Intl.DateTimeFormat` — `HH:mm`
  for 24 h, `MMM d HH:mm` for 7 d. No external date library.

### Added — Backend (UC-03)

- **`GET /api/dashboard/history?range=24h|7d&metric=gpu_temp|vram|calls|latency|tokens`**
  — uniform `{ series: [ { label, data: [ { ts, value } ] } ] }` shape
  across all five metrics. Auth gate: `current_user_must_be_settled`
  (same as the live dashboard — UC-09 password change must complete
  first). Validates `range` and `metric` against `Literal[…]`, so
  unknown values 422 from FastAPI's request-shape parser.
- **Aggregator service** (`services/aggregator.py`):
  - **`MinuteAggregator`** — runs every 60 s. Rolls 5 s
    `metrics_snapshot` rows into 1-min buckets in
    `metrics_snapshot_minute` via `INSERT OR IGNORE` keyed on
    `UNIQUE(bucket_ts, gpu_index)` (idempotent on re-run). Prunes raw
    `metrics_snapshot` rows older than 7 d.
  - **`HourAggregator`** — runs every 3600 s. Rolls 1-min buckets into
    1-h buckets in `metrics_snapshot_hour`. Prunes
    `metrics_snapshot_minute` rows older than 30 d.
  - Both expose `aggregate_once()` for tests + `run()` for the FastAPI
    lifespan, mirroring the existing `GpuSampler` / `ModelStateSampler`
    pattern. Defensive `try/except` swallows runtime errors so a single
    bad iteration doesn't kill the loop.
- **Cadence note (spec deviation):** the UC-03 functional spec describes
  the down-sample as an "hourly batch". We deliberately run the minute
  aggregator every 60 s (not hourly) so the 24 h chart updates within a
  minute of the most recent sample — an hourly batch would leave the
  most recent hour empty. Output granularity (1-min / 1-h buckets)
  matches the spec; only the *job* interval differs. Spec wording to be
  synced at sprint review.
- **Closed-minute / closed-hour semantics** — the in-progress wall-minute
  is never partially aggregated. If `now = 12:34:17`, the closed minute
  is `12:33:00 .. 12:34:00`; the current minute waits for the next tick.

### Migration

- **`0005_history.py`** — adds `metrics_snapshot_minute` +
  `metrics_snapshot_hour` (each with `UNIQUE(bucket_ts, gpu_index)` and
  `(gpu_index, bucket_ts)` indexes), plus a standalone `idx_messages_ts`
  so the call-rate / latency / tokens history queries can use an index
  (the existing composite `(conversation_id, ts)` can't serve a
  `ts`-only `WHERE` per the leftmost-prefix rule). Reversible.

### Descoped

- **Prompt-token histogram** and **daily completion-token total bar
  chart** — both listed in the UC-03 functional spec under the 7 d tab.
  Deferred to a UX polish sprint to keep the v0.3.0 surface focused on
  the four core time-series.
- **Per-user usage panel** mentioned in the use case (chat / code users
  see their own messages-sent-by-day chart) — also deferred. Today's
  history view is system-wide only.

### Tests

- **`tests/test_sprint8_history.py`** (29 tests):
  - migration round-trip + UNIQUE upsert
  - `MinuteAggregator`: averages, idempotent re-run, skips in-progress
    minute, empty-window, 7 d retention pruning, runtime-error swallowing
  - `HourAggregator`: hourly rollup from minute table, 30 d retention
    pruning, runtime-error swallowing
  - both `run()` periodic loops: cancel-cleanly + runtime-exception
    swallowing (mirrors the GpuSampler pattern)
  - `_percentile` helper edge cases (empty / single value / interpolated)
  - History endpoint: auth gate, range/metric validation, all five
    metrics × 24 h + 7 d coverage, user/system message exclusion,
    latency-null skipping, empty-data shape, partial-data shape (one
    GPU only).
- **394 tests collected, all green.** Coverage on touched modules:
  `routers/dashboard_history.py` 97 %, `services/aggregator.py` 98 %.

## [v0.2.1] — 2026-04-28 — Auth UX + Session control

Patch on top of v0.2.0. Adds five auth-surface improvements driven by
real-world operational needs after Sprint 6 landed: a perceptibly faster
chat send, voluntary password change for any user, a per-user JWT
lifetime preference, and admin-side session revocation + soft-deactivation
distinct from soft-delete. No new use cases — these slot under UC-01,
UC-04, UC-06, and UC-09.

### Added — Chat UX

- **Optimistic user-message rendering.** `ChatShell.sendMessage()` now
  pushes the user bubble into the conversation immediately on submit (with
  a sentinel `id=-1`) instead of waiting for the post-stream conversation
  refresh to confirm the row. Eliminates the dead second between hitting
  Enter and seeing your own text. The sentinel row is replaced by the
  authoritative server row when the conversation reloads after the stream
  closes.

### Added — Account self-service

- **`AppHeader` user menu.** The plain "Log out" button is now a dropdown
  showing `username · role`, with three actions:
  - **Change password** — link to `/change-password` (the same flow UC-09
    drives on forced change, but voluntary). Available to every role.
  - **Session duration** — `<select>` with **1 day / 7 days / 30 days /
    Unlimited** options. Calls `PATCH /api/auth/session-ttl`; the new
    preference applies on next login and to every sliding renewal from
    that point.
  - **Log out** — unchanged behaviour.

  The menu closes on outside-click and Escape; tab order matches visual
  order.

### Added — Per-user session TTL preference

- **`users.session_ttl_days`** column (nullable). `NULL` means "use the
  system default (7 days)"; otherwise must be one of `0` (effectively
  unlimited — 10 years), `1`, `7`, or `30`.
- **`PATCH /api/auth/session-ttl`** — the only endpoint that writes the
  column. Validates against the canonical `TTL_MAP` keys; rejects every
  other value with `422`. Login + sliding-renewal both consult
  `_user_ttl_seconds(user)` so the preference takes effect on the very
  next request after login.
- **`MeResponse.session_ttl_days`** — surfaced on `GET /api/auth/me` and
  in the login payload so the frontend dropdown can show the current
  setting without an extra round-trip.

### Added — Admin: force re-login (token revocation)

- **`users.token_version`** column (`NOT NULL DEFAULT 0`). Every JWT
  carries a `tkv` claim copied from the user's `token_version` at issue
  time; the auth dependency rejects any token whose `tkv` doesn't match
  the live row (`401 session_revoked`).
- **`POST /api/admin/users/{user_id}/revoke-sessions`** — bumps
  `token_version` by one. Every previously-minted token for that user
  fails the next request and the user lands on `/login`. Admins may
  revoke their own sessions (the next request bounces them to login,
  same as anyone else). Audit action `sessions_revoked`.

### Added — Admin: deactivate / reactivate

- **`users.is_active`** column (`NOT NULL DEFAULT 1`). Distinct from
  `deleted_at` — a deactivated account can be restored by an admin; a
  soft-deleted one is gone for good and the username can't be reused.
- **`POST /api/admin/users/{user_id}/deactivate`** — sets `is_active=0`
  **and** bumps `token_version` so existing sessions are invalidated
  immediately, not just blocked at next login. Refuses to deactivate the
  last *active* admin (`400 last_active_admin`). Idempotent (returns
  `{"already": "deactivated"}` if already off). Audit `user_deactivated`.
- **`POST /api/admin/users/{user_id}/reactivate`** — sets `is_active=1`.
  Doesn't bump `token_version` (the user has no live tokens to invalidate
  — the deactivate that triggered this already cleared them). Idempotent.
  Audit `user_reactivated`.
- **Login gate** — `POST /api/auth/login` now returns `403 account_disabled`
  for inactive accounts (audited as `login_blocked_inactive`) instead of
  silently issuing a token they can't use.
- **`/admin/users` page** — gains a per-row **Force re-login** button and
  a paired **Deactivate / Reactivate** button that swaps based on the
  row's `is_active` flag. Inactive rows render with a dimmed style and an
  "inactive" badge. The existing **Delete** button is unchanged.

### Changed — Authorization guards

- **`count_active_admins()`** now also requires `is_active = 1`. The
  existing UC-06 demote / delete guards (`cannot_demote_last_admin`,
  `cannot_delete_last_admin`) automatically pick up the new semantics
  along with the new deactivate guard — one count, three guards.

### Migration

- **`0004_auth_ux.py`** — adds `token_version`, `session_ttl_days`,
  `is_active` to `users`. All three carry sensible server defaults so
  existing rows back-fill without a separate UPDATE; the migration is a
  pure `ADD COLUMN` and reversible.

### Tests

- **`tests/test_sprint7_auth.py`** (26 tests):
  - `PATCH /session-ttl` — valid days (parametrized 0/1/7/30), invalid
    `14 → 422`, auth required, login uses new TTL after change, `/me`
    surfaces the new value.
  - `revoke-sessions` — invalidates outstanding token (`401
    session_revoked`), audit row shape, admin-only, `404` for missing
    user, admin can revoke self.
  - `deactivate` — blocks login (`403 account_disabled`), kicks existing
    session, last-active-admin guard (`400`), works when another active
    admin exists, idempotent, `404` for missing, admin-only.
  - `reactivate` — restores login, idempotent.
  - Defaults: `token_version=0`, `is_active=1`, `session_ttl_days=NULL`.
- **`tests/test_uc01_auth.py`** — `test_jwt_carries_only_sub_no_role`
  renamed to `test_jwt_carries_only_sub_tkv_exp_no_role`; asserts the
  three-key payload (`sub`, `tkv`, `exp`) and that `tkv` echoes the
  `token_version` argument.
- **365 tests collected total, all green.** Coverage on touched modules:
  `routers/auth.py` 95 %, `routers/admin_users.py` 96 %.

## [v0.2.0] — 2026-04-28 — User management + code workspace

Minor-version bump (UC-06): the cockpit gains a real admin user-management
surface and a per-user code working folder. UC-06 functional spec moves
through Accepted → In Progress → Done (technical) on this release.

### Added — User management (UC-06)

- **`/admin/users` page** — table with username · role · last login · lifetime
  tokens in / out · created · actions. Role inline-edited via dropdown;
  password reset and soft-delete via confirmation modals; "+ New user"
  modal with role selector. Native Intl.RelativeTimeFormat for the
  "2 hours ago" / "3 days ago" timestamps. Gated client-side and
  server-side to admin only; non-admins redirect to `/dashboard`.
- **`AppHeader` Users link** — visible to admins only.
- **Backend `/api/admin/users` router**:
  - `GET /` — list users + lifetime token totals (`tokens_in`,
    `tokens_out`) aggregated in one `GROUP BY conversation.user_id`
    query (no N+1). Filters: `?include_deleted=true`, `?q=<prefix>`.
  - `POST /` — create user with username regex `^[a-z][a-z0-9._-]{1,30}$`
    + bcrypt-hash + `must_change_password=1`. Audit `user_created`.
  - `PATCH /{id}/role` — change role. Refuses to demote the last admin
    (`409 cannot_demote_last_admin`). No-op for matching role (no audit).
  - `POST /{id}/reset-password` — admin reset; flips
    `must_change_password=1` + clears `password_changed_at`. Audit
    `password_reset_by_admin`.
  - `DELETE /{id}` — soft delete (`deleted_at = now()`). Refuses self
    (`409 cannot_self_delete`) and last admin
    (`409 cannot_delete_last_admin`). Audit `user_deleted`.
- **`services/users.py` additions** — `count_active_admins`,
  `create_managed_user`, `change_role`, `soft_delete`,
  `reset_password_admin`, `get_token_totals` (single user) and
  `get_token_totals_bulk` (all users in one query).

### Added — Code working folder (UC-06b)

- **Per-user workspace** at `<data_dir>/code_files/<username>/`. Created
  lazily on first access; configurable via `[paths] code_files_dir` in
  `config.toml` or `COCKPIT_CODE_FILES_DIR` env.
- **`/api/code/files` router**:
  - `GET /` — list files (non-recursive). `?dir=` walks subdirectories.
  - `GET /download?path=…` — file download with correct
    `Content-Disposition`.
  - `POST /save` — write a file atomically (`.tmp` → `os.replace`).
    `{path, content, overwrite}` body. 409 on `file_exists` when
    `overwrite=false`. 413 on > 10 MB.
  - `DELETE ?path=…` — file or empty directory. 409 on non-empty dir.
- **Path-traversal guard** — every operation runs through
  `_safe_user_path` which resolves the user-supplied relative path
  inside the per-user root and rejects `..` ladders, absolute paths,
  null-byte injection, and any path that resolves outside the root.
- **Code-page Files panel** — sidebar drawer below the conversation
  list; shows file name + size + Download / Delete actions. Refreshes
  on save.
- **Save-to-workspace button** — third icon button on every fenced code
  block in code mode (cloud-upload). Prompts for a filename (default
  `artifact.{language-ext}`); on 409 offers an Overwrite confirm; on
  success refreshes the Files panel.

### Changed

- `main.py` — registers `admin_users_router` under `/api/admin/users`
  and `code_files_router` under `/api/code/files`. The latter is
  registered **before** the UC-05 code router so the int-typed
  `/api/code/{conversation_id}` route doesn't shadow `/api/code/files`
  (would otherwise produce `422 int_parsing` on "files").

### Tests

- `tests/test_uc06_users.py` (28 tests): role gates, list filters +
  token aggregation, create / patch / reset / delete happy + error
  paths, last-admin guards, audit row shape.
- `tests/test_code_files.py` (26 tests): role gate, list / save /
  download / delete round-trip, sub-directory creation, overwrite
  semantics, 10 MB limit, six path-traversal variants, per-user
  isolation, atomic-rename failure cleans up `.tmp`.
- 339 tests collected total, all green.
- Coverage on the new modules: `routers/admin_users.py` 94%,
  `routers/code_files.py` 96%, `services/users.py` 94%.

## [v0.1.3] — 2026-04-28 — Dashboard GPU UX + model context display

UI-layer slice on top of v0.1.2. No new use cases or DB tables; falls under
the existing Accepted UC-02 functional spec.

### Added

- **GPU temperature status badge** on each card in the dashboard's GPU
  strip. Replaces the placeholder VRAM/temp gradient bar with a four-level
  status pill keyed off the RTX 3090 (Ampere GPU Boost 4.0) thresholds:
  - **Good** ≤ 70 °C — emerald.
  - **Workload** 71–82 °C — sky.
  - **Throttling** 83–89 °C — amber. GPU Boost starts clock reduction at
    ~83 °C.
  - **Critical** ≥ 90 °C — rose. Approaching TjMax 93 °C, shutdown risk.
  Raw °C is still shown alongside the badge so operators always have the
  number. Hover-title carries the threshold legend. When `temp_c` is null
  (no telemetry), neither badge nor temp line renders.
- **Watts vs. TDP** line on each GPU card:
  `152 W / 350 W` instead of just `152 W`. Current value is colour-coded
  (emerald ≤ 70 %, amber 71–90 %, rose > 90 %) by percentage of the cap.
  TDP comes from `nvidia-smi --query-gpu=power.limit`; falls back to 350 W
  when the column is `[N/A]`.
- **Configured context** line on every model card:
  `ctx 8 192` from `model_config.num_ctx_default`. Displays `ctx —` when
  no row exists or the value is null. Helps admins see each model's VRAM
  budget at a glance without opening the settings drawer.

### Backend

- `GpuSnapshot` (port) gains an optional `max_power_w: int | None`.
- `NvidiaSmiTelemetry` adapter extends its `--query-gpu=` argument with
  `power.limit`; parses the new column with the same `[N/A]` handling as
  the existing nullable columns. Float values are coerced to int.
- `GpuPayload` (Pydantic schema) and `_serialize_gpu` propagate
  `max_power_w` into the `/api/dashboard/snapshot` payload.
- `gpu_snapshot()` test factory in `adapters/fake_telemetry.py` accepts
  `max_power_w=None` by default; existing UC-02 tests build snapshots
  without specifying it and still validate.

### Tests

- `tests/test_uc02_telemetry.py`:
  - `test_sample_parses_canonical_two_gpu_csv` — the canonical CSV row
    now includes the `power.limit` column; assertion includes
    `max_power_w=350`.
  - `test_sample_handles_n_a_columns` — the `[N/A]` row gets a third
    `[N/A]` column; assertion includes `max_power_w is None`.
  - `test_sample_parses_power_limit_when_present` — explicit float
    coercion check (`350.00` → `350`).
- 286 tests collected, all green.

## [v0.1.2] — 2026-04-28 — Chat UX improvements + visual polish

UI-layer slice on top of Sprint 4. No new use cases, ports, or DB tables —
all changes fall under the existing Accepted UC-04 (chat) and UC-05 (code)
functional specs.

### Added

- **Copy button on every fenced code block.** Click → `navigator.clipboard`
  → checkmark confirmation for 1.5 s. Inline code unchanged.
- **Download button on `html`, `markdown`/`md`, `txt`, and `json` code
  blocks.** Client-side `Blob` → `<a download="artifact.{ext}">` → revoke.
  No backend round-trip.
- **Floating scroll-to-bottom button.** Appears via `IntersectionObserver`
  on the message-end anchor when the user has scrolled away from the
  latest reply. Smooth-scrolls back on click.
- **"Think" toggle** on the chat compose toolbar. Persists per-mode in
  `localStorage`. Pipes `think: true` into the stream request body, which
  the backend forwards as `options={"think": true}` to
  `LLMChat.chat_stream`. Models that don't recognise the option (most)
  ignore it silently per Ollama's docs.
- **Session token counter** below the compose box: live progress bar
  (neutral → amber at 80 % → rose at 95 %) plus exact `<used>/<limit>`
  count. Limit comes from `model_config.num_ctx_default` (now exposed on
  `ConversationDetail`); falls back to 8192.
- **Live ⏱ response timer** in the compose bar while the model is
  generating; updates every 100 ms. Frozen elapsed value shows up next to
  the conversation title as `Last response: 3.4 s` once the stream finishes.
- **Visual polish** — Claude-style two-column layout with a dark sidebar,
  light main pane, max-width message column, user-message bubbles with a
  cut bottom-right corner, no-bubble assistant messages with an orange
  avatar badge, syntax-highlighted code blocks
  (`react-syntax-highlighter` / `oneDark` theme) wrapped in a header bar
  carrying the language label + copy/download buttons, redesigned compose
  card with auto-grow textarea and an icon-only send button, and a
  blinking streaming cursor at the end of the in-flight assistant
  message.
- **Dark mode toggle** in `AppHeader` (sun / moon icon). Class-based
  Tailwind 4 strategy (`@custom-variant dark (&:where(.dark, .dark *))`)
  toggles the `.dark` class on `<html>`. Persists via `localStorage`;
  initial render respects the system preference.

### Backend

- `StreamRequest` schema gains `think: bool = False`.
- `stream_reply()` accepts `options: dict | None` and forwards verbatim to
  `LLMChat.chat_stream(options=...)`.
- `ConversationDetail` schema gains `num_ctx_default: int | None`,
  populated by joining the `model_config` row for the conversation's
  current model. `null` when no row exists.

### Tests

- `test_think_true_passes_through_to_chat_stream_options` and
  `test_think_false_omits_options_dict` in `tests/test_uc04_chat.py`.
- `test_conversation_detail_includes_num_ctx_default` (and the null
  case) confirm the new schema field round-trips through the DB join.
- 285 tests collected total (was 281); all green.

## [v0.1.1] — 2026-04-28 — SQLite WAL + embedding model fix

Bug fixes from the first Neuroforge live install.

### Fixed

- **`GpuSampler` `database is locked` errors.** The 5-second background
  insert into `metrics_snapshot` deadlocked against concurrent request
  handlers because SQLite's default journal mode allows only one writer.
  `make_engine` now enables `PRAGMA journal_mode=WAL` and
  `PRAGMA busy_timeout=5000` for every SQLite connection via a
  SQLAlchemy connect-event listener.
- **Performance test crash on embedding-only models.** Models like
  `nomic-embed-text:latest` return HTTP 400 "does not support chat" on
  `chat_stream`. `_drop_model` and the perf-harness `cold_load` stage
  now catch `OllamaResponseError` and emit a clean
  `{"detail": "model_not_supported"}` SSE error event instead of
  bubbling up to the ASGI stack as an `ExceptionGroup`.

## [v0.1.0a2] — 2026-04-28 — Sprint 4: chat + code + Next.js frontend

First **pip-installable** release. Install with:

```
pip install git+https://github.com/Bloxperts/llm-cockpit.git@v0.1.0a2
cockpit-admin init
cockpit-admin serve
```

Open the cockpit at `http://localhost:8080/`, log in as `admin / ollama`,
change the password on first login, land on the live dashboard.

### Added

- **UC-07 — `LLMChat` port + `OllamaLLMChat` adapter.** All five methods
  (`list_models`, `loaded`, `chat_stream`, `pull_model`, `delete_model`)
  with HTTPX async client, the spec's 5 s connect / 900 s read timeouts,
  and parametrised wire-shape contract tests pinning the keys we read
  from Ollama's NDJSON.
- **UC-08 — install + bootstrap + serve.** `cockpit-admin {init, migrate,
  doctor, serve}` and the bundled Next.js frontend.
- **UC-01 — login.** JWT in HttpOnly + SameSite=Strict cookie, sliding
  renewal, 5-fail-per-username lockout, login_audit writes, ADR-004 role
  ladder (chat < code < admin).
- **UC-09 — first-login forced password change.** `current_user_must_be_settled`
  dependency + `POST /api/auth/change-password` validating length ≥ 8,
  match, and not the literal default.
- **UC-02 — live dashboard + placement board.** Telemetry port +
  `NvidiaSmiTelemetry` adapter, two background samplers, snapshot endpoint
  + SSE stream, full admin Ollama router (place / perf-test / pull /
  delete / settings) with single-flight enforcement (ADR-005 §5).
- **UC-04 — chat interface.** Per-user conversations, streaming SSE
  replies, regenerate-last-turn, partial save on disconnect.
- **UC-05 — code interface.** Same shell with `code` role gate +
  default system prompt from settings or bundled fallback.
- **Next.js frontend.** Static export bundled into the wheel — login,
  forced password change, dashboard with placement controls, chat, code
  pages all functional. Renders Markdown + GFM via `react-markdown`.
- **Build tooling.** `Makefile` + `scripts/build-frontend.sh` orchestrate
  the Next.js build → `src/cockpit/frontend_dist/` copy → `python -m
  build` → sdist + wheel.

### Architecture / process

- ADR-001..005 accepted; PROCESS.md v1.0; three-doc spec form (use case
  + functional + test) for every UC; develop / main two-branch flow.
- Three migrations: `0001_initial` (six bootstrap tables), `0002_dashboard`
  (`metrics_snapshot` + `admin_audit`), `0003_chat` (`conversations` +
  `messages`).

### Tests

- 281 automated tests across 11 files. ≥ 90 % coverage on every module
  the SPRINT_STATE.md review checklist tracks.

### Known limitations

- Drag-and-drop placement board is wired as a `<select>` per card in this
  alpha; full `dnd-kit` interaction lands in v0.1.0a3.
- `last_calls` panel on the dashboard returns `[]` — populated once chat
  history surfaces in UC-03 (Sprint 5).
- No telemetry adapter beyond `nvidia-smi`. Apple Silicon / AMD users
  see "no GPU telemetry" — v0.2.
