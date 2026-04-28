# Claude Code prompt — Sprint 9: UC-10 Admin Ollama configuration

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

`develop` HEAD is the v0.3.0 merge (after Sprint 8). Confirm:

```bash
git fetch origin && git log origin/develop --oneline -3
```

---

## Read first

1. `CLAUDE.md`
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-10-ollama-configuration.md` — **Accepted**. Primary reference.
4. `src/cockpit/routers/admin_ollama.py` — what already exists (pull, delete, place, perf-test, settings patch).
5. `src/cockpit/services/model_tags.py` — existing tag heuristic logic.
6. `src/cockpit/models.py` — `ModelTag`, `Settings`, `ModelPerf`, `Message`.
7. `src/cockpit/routers/admin_users.py` — pattern to follow for the audit endpoint.

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-10-admin-ollama-config
```

Commit prefix: `[UC-10]`. One PR against `develop`.

---

## What already exists — do not rebuild

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

## Step 0 — Fill in test spec

`docs/specs/test/UC-10-ollama-configuration.md` is a stub. Fill in approach +
test cases covering every AC. Flip to `Accepted`. Commit: `[UC-10] fill in test spec`.

---

## Step 1 — New / missing backend endpoints

All routes in `src/cockpit/routers/admin_ollama.py` unless noted.
All require `Depends(require_role("admin"))`.

### 1a — Model tag management

**`PATCH /api/admin/ollama/models/{model}/tag`**

Body: `{ "tag": "chat" | "code" | "both" }`

- Upsert `ModelTag(model=model, tag=body.tag, source='override')`.
- Write `AdminAudit(action='model_tag_set', target_model=model, details={'tag': body.tag})`.
- Re-evaluate heuristics for all models (call existing `model_tags.py` logic).
- Return `{ "model": model, "tag": body.tag, "source": "override" }`.

**`DELETE /api/admin/ollama/models/{model}/tag`**

- Delete the `ModelTag` row for this model (removes the override).
- Re-apply heuristic: compute the auto tag and insert a new row with `source='auto'`.
- Write audit `action='model_tag_cleared'`.
- Return 204.

### 1b — Settings GET/PUT

**`GET /api/admin/ollama/settings`**

- Return `{ "code_default_system_prompt": str | null, "tag_heuristics_yaml": str | null }`.
- Read from `Settings` table by key. Return `null` if the key doesn't exist yet.

**`PUT /api/admin/ollama/settings`**

Body: `{ "code_default_system_prompt"?: str, "tag_heuristics_yaml"?: str }`

- For each provided key: upsert the `Settings` row.
- If `tag_heuristics_yaml` is updated: re-evaluate all model tags immediately
  (call the heuristic resolver over the current `LLMChat.list_models()` result).
  Use `app.state.model_state.available_models` (already cached) to avoid hitting
  Ollama on every settings save.
- Write `AdminAudit(action='settings_updated', details={keys_changed})`.
- Return `{ "updated": [list of keys changed] }`.

### 1c — Per-model metrics endpoints

**`GET /api/admin/ollama/metrics`**

Returns a rollup per model for the last 7 days. Query against `messages` table:

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

Note: SQLite has no `PERCENTILE_DISC`. Omit p95 from this rollup (spec mentions it
but it requires pulling all rows — too expensive for the list view). Include it in
the drill-down (1c).

Response: `list[ModelMetricsSummary]` — define Pydantic schema in `schemas.py`.

**`GET /api/admin/ollama/metrics/{model}`**

Returns the last 50 completed calls for a specific model:

```sql
SELECT role, usage_in, usage_out, latency_ms, gen_tps, ts, error
FROM messages
WHERE model = :model AND role = 'assistant'
ORDER BY ts DESC
LIMIT 50
```

Also compute p95 latency in Python from the returned rows.

Response: `{ "calls": [...], "p95_latency_ms": float | null }`.

### 1d — Audit log endpoint

**`GET /api/admin/audit`**

Merges `login_audit` and `admin_audit` into a unified paginated feed:

Query params: `?page=1&per_page=50&action=&username=`

Response:
```python
class AuditEntry(BaseModel):
    source: str   # "login" | "admin"
    ts: datetime
    actor: str | None       # username if resolvable
    action: str
    target: str | None      # username or model name
    details: dict | None
    source_ip: str | None

class AuditResponse(BaseModel):
    entries: list[AuditEntry]
    total: int
    page: int
    per_page: int
```

Implement with a UNION query or two separate queries merged and sorted in Python
(simpler with SQLite which doesn't handle UNION + ORDER BY + LIMIT well across
heterogeneous schemas).

Also add a **CSV export** endpoint:
`GET /api/admin/audit/export` — same filters, no pagination, returns
`text/csv` with `Content-Disposition: attachment; filename=audit.csv`.

Register all new endpoints in `main.py` under the existing `/api/admin/ollama`
prefix (metrics + tag) and a new `/api/admin/audit` prefix (audit log).

---

## Step 2 — Heuristic re-evaluation on model tag save

In `src/cockpit/services/model_tags.py`, ensure there is a function:

```python
def reapply_heuristics(
    session: Session,
    available_models: list[ModelInfo],
    yaml_override: str | None = None,
) -> None:
    """Re-evaluate tag heuristics for all models. Override rows are untouched.
    Auto rows are updated. Called after settings save or new model detection."""
```

Call this from the `PUT /api/admin/ollama/settings` handler when
`tag_heuristics_yaml` changes, and also wire it into `ModelStateSampler.sample_once()`
when new models are detected (compare current available model names against
the set in `app.state.model_state.available_models`).

---

## Step 3 — Frontend: `/admin/ollama` page

Create `frontend/src/app/admin/ollama/page.tsx`.

Gate: `role === 'admin'` only.

Add **"Ollama"** link in `AppHeader.tsx` (admin-only, alongside the existing "Users" link).

### Four collapsible panels (use `<details>/<summary>` or a simple accordion —
no new UI library needed)

**Panel 1 — Model tags**

Table: `Model | Size | Tag | Source | Actions`

- Fetch from existing `GET /api/dashboard/snapshot` (already has model list + tags)
  OR add a dedicated `GET /api/admin/ollama/models` endpoint that returns the same
  data. Use the snapshot — it's already cached and avoids a new endpoint.
- Tag column: `<select>` with options chat/code/both. On change: `PATCH .../tag`.
- Source badge: `auto` (neutral) / `override` (amber).
- "Clear override" button (only visible when `source === 'override'`):
  `DELETE .../tag`.
- Pull new model: a text input + "Pull" button at the bottom of the panel. On submit:
  opens a `<dialog>` and streams SSE from `POST .../pull`, showing progress lines.
- Delete button per row: confirmation dialog → `DELETE` endpoint.

**Panel 2 — Defaults**

Two text areas:
- **Code system prompt** (`code_default_system_prompt`): plain `<textarea rows=8>`.
- **Tag heuristics YAML** (`tag_heuristics_yaml`): monospace `<textarea rows=12>`.

"Save" button: `PUT /api/admin/ollama/settings`. Show success/error flash.

**Panel 3 — Per-model metrics (last 7 days)**

Sortable table: `Model | Calls | Prompt tokens | Completion tokens | Avg latency | Avg TPS | Last call`

Fetch from `GET /api/admin/ollama/metrics`. Refresh button.

Click a row → opens a `<dialog>` with:
- Last 50 calls table: `Timestamp | Tokens in | Tokens out | Latency ms | TPS | Error`
- p95 latency shown above the table.

**Panel 4 — Audit log**

Paginated table: `Time | Source | Actor | Action | Target | IP`

- Fetch from `GET /api/admin/audit?page=1&per_page=50`.
- Filter inputs: Action (text) + Username (text). Apply button re-fetches.
- "Export CSV" button → navigates to `GET /api/admin/audit/export` with current
  filters (browser handles the download).
- Pagination: Prev / Next buttons.

---

## Step 4 — Spec status edits

- `docs/specs/functional/UC-10-ollama-configuration.md`: `Accepted → In Progress`
  at branch open, `Done (technical)` when tests pass.

---

## Coverage target

```bash
pytest --cov=cockpit.routers.admin_ollama \
       --cov=cockpit.services.model_tags \
       --cov-report=term-missing
```

≥ 90 % on both modules. All 365 prior tests must stay green.

---

## Build + release

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
- Settings editor: code system prompt + tag heuristics YAML (change takes effect immediately)
- Per-model metrics: 7-day rollup table + last 50 calls drill-down with p95 latency
- Audit log: merged login + admin events, filterable, CSV export"
```

---

## Stop and ask Chris if

- `GET /api/dashboard/snapshot` is sufficient as the model-list source for Panel 1,
  or if a dedicated endpoint is cleaner — the snapshot is already cached so it's fast.
- The UNION query for the audit log is too slow on large datasets — if `login_audit`
  has millions of rows (unlikely in v0.1), fall back to separate queries with a
  combined sort in Python.
- The tag heuristics YAML editor needs syntax validation in the UI — a try/except
  on `yaml.safe_load` server-side on save is sufficient for v0.1; a client-side
  YAML linter is a nice-to-have for later.
