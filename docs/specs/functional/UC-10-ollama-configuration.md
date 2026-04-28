<!-- Status: In Progress | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-10 · Functional Spec — Admin: Ollama configuration + metrics

**Status:** In Progress
**Depends on:** UC-01 (login), UC-07 (Ollama integration — `pull_model` and `delete_model` are on the same `LLMChat` port), ADR-004 (admin role + model tagging).
**Min role:** `admin`.
**User Spec:** [`../../use-cases/UC-10-ollama-configuration.md`](../../use-cases/UC-10-ollama-configuration.md)
**Test Spec:** [`../test/UC-10-ollama-configuration.md`](../test/UC-10-ollama-configuration.md)
**Bound DG:** DG-004 — extends UC-07's `LLMChat` port with `pull_model` / `delete_model` write methods. Block at end of file.

## Goal

A single admin page that lets the cockpit's admin keep Ollama tidy without SSH'ing in: tag models chat / code / both, pull new models, delete unused models, edit the default code-mode system prompt, and inspect metrics + audit log.

## Data model additions

```sql
CREATE TABLE model_tags (
  model     TEXT PRIMARY KEY,
  tag       TEXT NOT NULL CHECK (tag IN ('chat', 'code', 'both')),
  source    TEXT NOT NULL CHECK (source IN ('auto', 'override')),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- known keys:
-- 'code_default_system_prompt'  → text body
-- 'tag_heuristics_yaml'         → YAML body editable from the UI
```

## API

```
GET    /api/admin/ollama/models               → list of {model, size_gb, loaded, tag, source} from
                                                  LLMChat.list_models() ⨝ LLMChat.loaded() ⨝ model_tags
PATCH  /api/admin/ollama/models/{model}/tag   → 200 { tag, source: 'override' }            body { tag }
DELETE /api/admin/ollama/models/{model}/tag   → 204    (clears override; falls back to heuristic)
POST   /api/admin/ollama/models/{model}/pull  → SSE of PullProgress chunks                  uses LLMChat.pull_model
DELETE /api/admin/ollama/models/{model}       → 204                                         uses LLMChat.delete_model

GET    /api/admin/ollama/settings             → { code_default_system_prompt, tag_heuristics_yaml }
PUT    /api/admin/ollama/settings             body { key, value }                          → 200

GET    /api/admin/ollama/metrics              → per-model metrics rollup, last 7 d          (admin only)
GET    /api/admin/ollama/metrics/{model}      → drill-down: last 50 calls (no message body) (admin only)
GET    /api/admin/audit                        → merged login_audit + admin_audit          (admin only)
```

All routes gated by `Depends(require_role("admin"))` per ADR-004.

## Frontend layout

- `/admin/ollama` — four panels (collapsible), as described in the User Spec:
  1. **Model tags** — table of models with tag column + override action. Side drawer for `pull` progress.
  2. **Defaults** — text area for `code_default_system_prompt`, code-editor for `tag_heuristics_yaml`. Save button per panel.
  3. **Per-model metrics** — sortable table; click row → drawer with last 50 calls.
  4. **Audit log** — paginated, filterable, CSV export.

## Heuristic-to-override resolution

For any model `M`:

1. If `model_tags(M).source = 'override'` → return that row's tag.
2. Else if heuristic regex from `settings('tag_heuristics_yaml')` matches `M.name` → tag is the matched group's value, source `'auto'`. Insert/update `model_tags` accordingly.
3. Else default to `chat` with source `'auto'`.

The heuristic is re-evaluated on:

- Bootstrap (`cockpit-admin init`).
- Every `GET /api/admin/ollama/models` call (cheap).
- Save of `tag_heuristics_yaml` setting.
- New model appearing in `LLMChat.list_models()` (cockpit polls every 5 min in the background).

## Default heuristic YAML (shipped in `default_config/`)

```yaml
# Match against model name (lowercased). First match wins.
rules:
  - pattern: "(coder|code-|codellama|deepseek-r1-coder|qwen2\\.5-coder|starcoder|wizardcoder|phind|magicoder)"
    tag: code
  - pattern: ".*"
    tag: chat
```

The admin can edit this YAML in the UI; on save the cockpit re-runs over all known models and updates `model_tags` rows whose `source='auto'`. Override rows are untouched.

## Per-model metrics SQL (sketch)

```sql
SELECT m.model,
       COUNT(*)                              AS calls,
       SUM(m.usage_in)                       AS prompt_tokens,
       SUM(m.usage_out)                      AS completion_tokens,
       AVG(m.latency_ms)                     AS mean_latency_ms,
       PERCENTILE_DISC(0.95) WITHIN GROUP    -- via SQLite percentile_cont if available; else CTE
            (ORDER BY m.latency_ms)          AS p95_latency_ms,
       AVG(m.gen_tps)                        AS mean_gen_tps,
       MAX(m.ts)                             AS last_call_at
FROM messages m
WHERE m.ts >= datetime('now', '-7 days')
GROUP BY m.model
ORDER BY calls DESC;
```

(SQLite ≥ 3.42 has `percentile_cont`; older SQLites use a CTE-based approximation.)

## Acceptance criteria

- See User Spec §Acceptance criteria. Test Spec automates each.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. Two boundary surfaces.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| Ollama daemon | Read + write (pull / delete) | `LLMChat` (extended methods `pull_model`, `delete_model`) | `OllamaLLMChat` (UC-07) | Outbound |
| `nvidia-smi` (optional) | Read (history) | `Telemetry` | `NvidiaSmiTelemetry` (UC-02) | Outbound |

**Why the same `LLMChat` port covers `pull_model` and `delete_model`:**

- They are still Ollama-specific operations on the same daemon.
- A separate `ModelLifecycle` port was rejected (DP-007). It would force two adapters to share state about the same backend.
- Routers use a *narrower* dependency: `admin_ollama` injects `LLMChat` but only calls the lifecycle methods. Tests can swap `FakeLLMChat` to validate behaviour without a real Ollama.

**Test seam:** `FakeLLMChat` from UC-07 already supports `pull_model` and `delete_model`. Tests pin the side effects (model now appears, model now gone) by inspecting the fake's recorded calls.

**Compliance:** DP-029 (hexagonal) — port + adapter still apply for the new methods; DP-002 (debuggability) — every state-changing action writes an `admin_audit` row; DP-014 (governance & budget contracts) — pull-progress and delete are the closest v0.1 has to budget contracts (admin makes a deliberate decision); DP-031 (progressive autonomy) — admin role gates the whole router.
