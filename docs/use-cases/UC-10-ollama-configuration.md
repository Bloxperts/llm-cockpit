<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-10 · Use Case — Admin: Ollama configuration + metrics

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`../specs/functional/UC-10-ollama-configuration.md`](../specs/functional/UC-10-ollama-configuration.md)
**Test Spec:** [`../specs/test/UC-10-ollama-configuration.md`](../specs/test/UC-10-ollama-configuration.md)
**Sprint:** 7
**Depends on:** UC-01 (login), UC-07 (Ollama integration), ADR-004 (admin role), ADR-005 (per-model lifecycle).
**Min role:** `admin`.

## Story

> As the `admin` I want a configuration page that holds the controls that don't belong on the placement board: editing the chat / code tagging heuristic, setting the default code-mode system prompt, drilling into a model's full performance-test history, and filtering the audit log — so that I have a tidy admin surface for the administrative work that doesn't fit on the dashboard.

> The placement board (UC-02) handles everyday lifecycle (place, perf-test, pull, delete, per-model settings). This page is for the things that don't fit on cards.

## Target state

`/admin/ollama` shows four panels:

### 1. Tagging

- **Heuristic regex editor.** Multi-line text area mounted on the YAML file `config/model_tag_heuristics.yaml`. Save → re-runs heuristic across all models whose `model_tags.source = 'auto'` and updates them.
- **Per-model tag override table.** Lists every model Ollama currently serves with `name`, current `tag`, `source` (`auto` / `override`). Inline dropdown to override per row; "clear override" button drops back to heuristic.

### 2. Code-mode default system prompt

- Single multi-line text area, persisted in `settings('code_default_system_prompt')`. Edits propagate to new `/code` conversations on save (existing conversations untouched).

### 3. Performance-test history

- Per-model expandable rows. Each row is a model; expand reveals last N `model_perf` rows: `measured_at`, `cold_load_seconds`, `throughput_tps`, `max_ctx_observed`, `gpu_layout_json`, `notes`.
- "Run perf test" button on each row — same harness as the card-action on UC-02, surfaced here for one-stop deeper analysis.
- "Compare" toggle — pick two perf rows for the same model and view a side-by-side diff (e.g. before / after a model upgrade).

### 4. Audit log

- Merged feed of `login_audit` and `admin_audit`, time-sorted descending.
- Filters: actor (username), action (`model_place`, `model_pulled`, `model_deleted`, `user_added`, `user_set_role`, `user_set_password`, `user_deleted`, `password_changed`, `login`, `setting_update`, `model_tag_set`), date range.
- "Export CSV" button.

## Acceptance criteria

1. Only `admin` reaches `/admin/ollama`. Lower roles get 403; sidebar link hidden.
2. Editing the heuristic YAML and saving: every model with `source='auto'` is re-evaluated; rows whose computed tag changed get a toast notification ("`mistral:7b` → tag changed `chat` → `code` after rule update"). Override rows untouched.
3. Setting an override on a model: takes effect immediately, the chat / code picker reflects the change on next refresh, an `admin_audit` row is written.
4. Editing the code-mode default system prompt: a new `/code` conversation started after the edit shows the new prompt as its seed; existing conversations retain whatever prompt they had at creation.
5. The performance-test history panel correctly groups perf-test rows by model; "Run perf test" produces a new row with sensible values within ~30 s on a model that loads in under 15 s.
6. The audit log shows correct rows with actor names resolved (or `<deleted>` for soft-deleted users) and supports the four filters.
7. Every state change on this page writes a row to `admin_audit`.

## Scope boundaries (out)

- **Anything the placement board (UC-02) already covers:** drag-drop placement, on-board perf-test, "+ Add model", per-model settings (keep_alive / num_ctx / single_flight) — these belong on UC-02 and are not duplicated here. The UC-10 perf-test "Run" button is a convenience link to the same harness for admins who're already in the deeper history view.
- GPU power-cap controls. Out of scope — not portable.
- vLLM / multi-backend management. Out of scope.
- Sudo / root operations of any kind. Out of scope.

## Notes

- Per ADR-005 the everyday model lifecycle lives on UC-02. UC-10 is now strictly the "deeper admin" surface.
- DG-004 binding: this UC reaches Ollama only via the perf-harness "Run" button, which uses the same `LLMChat` port + same lock as UC-02. No new boundary surface.
