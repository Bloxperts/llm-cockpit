<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-10 · Test Spec — Admin: Ollama configuration + metrics

**Status:** Accepted
**User Spec:** [`../../use-cases/UC-10-ollama-configuration.md`](../../use-cases/UC-10-ollama-configuration.md)
**Functional Spec:** [`../functional/UC-10-ollama-configuration.md`](../functional/UC-10-ollama-configuration.md)

## Approach

UC-10 is admin-surface-only — no boundary surface beyond what UC-07 /
UC-02 already cover. Test layers:

1. **Tag CRUD** — `PATCH /api/admin/ollama/models/{model}/tag` and the
   matching `DELETE`. Override semantics (`source='override'`), audit
   row shape, idempotent override on identical tag, heuristic
   re-application on `DELETE`. The heuristic uses
   `app.state.model_state.available_models` so tests prime that state
   via the `FakeLLMChat` adapter.
2. **Settings GET / PUT** — `code_default_system_prompt` and
   `tag_heuristics_yaml`. Round-trip read after write; partial body
   patches one key without affecting the other; YAML validation rejects
   malformed input with `400`. PUT writes one `admin_audit` row keyed
   to the changed keys.
3. **Per-model metrics** — `GET /api/admin/ollama/metrics` aggregates
   over the last 7 days from `messages` (assistant rows only).
   `GET /api/admin/ollama/metrics/{model}` returns the last 50 rows
   plus the Python-computed p95 latency.
4. **Unified audit log** — `GET /api/admin/audit` merges
   `login_audit` + `admin_audit` into a single time-sorted feed,
   paginated, filterable by `action` and `username`. CSV export is a
   sibling endpoint with the same filters.
5. **Heuristic re-evaluation** — `services.model_tags.reapply_heuristics`
   walks the available-model list, resolves the auto-tag for each row
   whose `source='auto'`, and leaves override rows untouched. Called
   from PUT settings (when the YAML key changes) and from
   `ModelStateSampler.sample_once()` when a never-before-seen model
   name appears.

All routes round-trip via FastAPI `TestClient` over an in-memory
SQLite. `FakeLLMChat` from UC-07 supplies the model list. No frontend
tests in v0.1 (Vitest still out of scope).

## Test cases

### Auth gate (3 tests)

- **`test_admin_ollama_routes_reject_chat_user`** — `chat` role gets
  `403` on every UC-10 endpoint.
- **`test_admin_ollama_routes_reject_code_user`** — `code` role same.
- **`test_admin_ollama_routes_reject_unauthenticated`** — no cookie → `401`.

### Tag CRUD (8 tests)

- **`test_patch_tag_creates_override_row`** — creates / updates
  `model_tags` row with `source='override'`; returns `{ model, tag,
  source: "override" }`.
- **`test_patch_tag_writes_audit`** — one `admin_audit` row, action
  `model_tag_set`, `target_model` set, `details_json` carries the new
  tag.
- **`test_patch_tag_idempotent`** — patching with the same tag twice
  produces one row in `model_tags` and one audit per call.
- **`test_patch_tag_rejects_invalid_tag`** — body `{ tag: "weird" }` →
  `422`.
- **`test_delete_tag_removes_override_and_reapplies_heuristic`** — after
  `DELETE` the row's `source='auto'` and the tag is whatever the
  heuristic yields.
- **`test_delete_tag_writes_audit`** — action `model_tag_cleared`,
  target matches.
- **`test_delete_tag_no_existing_override_is_idempotent`** — `DELETE`
  on a row that doesn't have an override returns `204`, no audit row.
- **`test_delete_tag_unknown_model_returns_204`** — endpoint is
  idempotent on names Ollama isn't currently serving.

### Settings GET / PUT (6 tests)

- **`test_get_settings_returns_nulls_when_unset`** — both keys NULL on
  fresh DB.
- **`test_put_settings_writes_both_keys`** — body
  `{ code_default_system_prompt, tag_heuristics_yaml }` → both rows
  present, `updated` array reflects both.
- **`test_put_settings_partial_body_only_writes_supplied_keys`** —
  body with only `code_default_system_prompt` leaves
  `tag_heuristics_yaml` unchanged.
- **`test_put_settings_invalid_yaml_returns_400`** — malformed YAML
  → `400 invalid_yaml`. No row written. Pre-existing row preserved.
- **`test_put_settings_writes_audit`** — one `admin_audit` row, action
  `settings_updated`, `details_json` lists the changed keys.
- **`test_put_settings_yaml_change_reapplies_heuristics`** — saving a
  new YAML triggers `reapply_heuristics()` over the cached model list;
  `auto`-source rows are updated; `override` rows are not.

### Per-model metrics (5 tests)

- **`test_metrics_summary_aggregates_last_7_days`** — synthetic
  messages spanning 8 days → only the last 7 days' rows roll up; older
  rows excluded.
- **`test_metrics_summary_excludes_user_and_system_rows`** — only
  `role='assistant'` is counted.
- **`test_metrics_summary_orders_by_calls_desc`** — model with more
  calls comes first.
- **`test_metrics_drilldown_returns_last_50_calls`** — 60 messages →
  endpoint returns 50, newest first.
- **`test_metrics_drilldown_p95_latency_python_computed`** — known
  latency_ms list yields a known p95 (linear interpolation).

### Audit log (5 tests)

- **`test_audit_merges_login_and_admin_rows`** — DB seeded with one
  login row + one admin row → both appear, sorted ts-desc.
- **`test_audit_filters_by_action`** — `?action=model_pulled` returns
  only that subset.
- **`test_audit_filters_by_username`** — `?username=alice` → rows where
  alice is the actor (admin) or the username (login).
- **`test_audit_pagination`** — `?per_page=5&page=2` returns rows 6-10
  with correct `total` / `page` / `per_page`.
- **`test_audit_csv_export`** — `Content-Type: text/csv`,
  `Content-Disposition: attachment; filename=audit.csv`. Header row +
  one row per audit entry, no pagination.

### `reapply_heuristics()` helper (3 tests)

- **`test_reapply_heuristics_updates_auto_rows`** — model with
  `source='auto'` is updated when its computed tag changes.
- **`test_reapply_heuristics_skips_override_rows`** — model with
  `source='override'` is untouched even if the YAML changed.
- **`test_reapply_heuristics_handles_yaml_override_arg`** — passing a
  `yaml_override` string uses that YAML rather than the persisted
  setting.

## Pass criteria

- All automated tests pass on `develop` and `main`.
- ≥ 90 % coverage on `routers/admin_ollama.py` and `services/model_tags.py`.
- 394 prior tests stay green.
- Manual smoke at sprint review: log in as admin, open `/admin/ollama`,
  exercise each panel. Non-admin users see `403` and the sidebar link
  is hidden.
