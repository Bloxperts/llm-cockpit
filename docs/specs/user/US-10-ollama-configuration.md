<!-- Status: Review | Version: 0.1 | Created: 2026-04-27 -->
# US-10 · User Spec — Admin: Ollama configuration + metrics

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-10-ollama-configuration.md`](../functional/US-10-ollama-configuration.md)
**Test Spec:** [`test/US-10-ollama-configuration.md`](../test/US-10-ollama-configuration.md)
**Sprint:** 7
**Depends on:** US-01 (login), US-07 (Ollama integration — pull/delete are on the same `LLMChat` port), ADR-004 (admin role + model tagging).
**Min role:** `admin`.

## Story

> As the `admin` I want a configuration page that lets me decide which models are tagged "chat" / "code" / "both", pull new models from the Ollama registry, delete models, edit the default code-mode system prompt, and see deeper metrics (per-model usage, audit log, GPU history) than the dashboard shows, so that I can run the cockpit without ever shelling into the host or editing config files.

## Target state

`/admin/ollama` shows four panels:

### 1. Model tags

Table of every model Ollama is currently serving, plus models we have metadata about even if not currently loaded:

| name | size_gb | currently loaded | tag | actions |
|------|--------|------------------|-----|---------|
| gemma3:27b | 16.4 | yes | `chat` (auto) | [override] [pull] [delete] |
| qwen3-coder:30b | 18.0 | yes | `code` (auto) | [override] [pull] [delete] |
| llama3.1:70b | 40.2 | no | `chat` (override → admin) | [override] [pull] [delete] |

- `tag` cell shows the resolved tag and where it came from (`auto` from heuristic, `override` from admin).
- `[override]` opens a small select: `chat` / `code` / `both` / `clear override (use heuristic)`.
- `[pull]` re-pulls the model (Ollama's `/api/pull`); progress streamed via SSE in a side drawer.
- `[delete]` removes the model from Ollama (`/api/delete`); confirm modal because this is destructive.

### 2. Defaults

- **Code-mode default system prompt.** Multi-line text area. Edits save to a `settings` table row. The `/code` page (US-05) reads this on each new conversation as the seed system prompt.
- **Heuristic regex list** (advanced). Read from `config/model_tag_heuristics.yaml`; admin can edit and save. Re-runs heuristic over all models on save. The `[clear override]` action falls back to whatever the heuristic now says.
- **Default `keep_alive`** (display-only in v0.1, read-only). The cockpit does **not** push `keep_alive` to Ollama in v0.1 — that's the v0.2 "Model Lifecycle" story. Shown here so admins know where it'll live later.

### 3. Per-model metrics

For each model used in the last 7 days:

- Total calls
- Total prompt tokens / completion tokens
- Mean / p95 first-token latency
- Mean / p95 generation tokens-per-second
- Last call timestamp

Sortable by any column. Click a row → drawer with that model's last 50 calls (admin-only, includes who called it but not message bodies).

### 4. Audit log

Last 200 rows from `admin_audit` and `login_audit`, merged and time-sorted. Filter by actor, by action, by date range. Export to CSV button.

## Acceptance criteria

1. Only `admin` reaches `/admin/ollama`. Lower roles get 403; sidebar link hidden.
2. Heuristic auto-tag classifies obvious cases correctly out of the box: `qwen3-coder:30b`, `qwen2.5-coder:7b`, `codellama:13b`, `deepseek-r1-coder:33b` → `code`; `llama3.1:70b`, `gemma3:27b`, `mistral:7b`, `phi4:14b` → `chat`.
3. Admin override on a model flips its tag immediately; the chat / code page picker reflects the change on next refresh.
4. Pulling a new model streams progress in the side drawer; on completion, the model appears in the table tagged per the heuristic.
5. Deleting a model removes it from Ollama (verifiable via `ollama list` on the host) and from the model-tags table; the model can no longer be picked for new conversations. Past conversations using that model remain in history.
6. Editing the default code-mode system prompt: a new `/code` conversation started after the edit shows the new prompt as its seed system prompt. Existing conversations are unaffected.
7. Per-model metrics are computed via SQL queries against `messages` table; refresh on demand (no polling).
8. Audit log shows correct rows with actor names resolved from `users` (or `<deleted>` for soft-deleted users).
9. Every action on this page that changes state writes a row to `admin_audit` with `actor`, `action`, `target`, `details_json`.

## Scope boundaries (out)

- Pin / unpin model (force-keep in VRAM). v0.2 (Model Lifecycle).
- Set per-model `keep_alive`. v0.2 (Model Lifecycle).
- Set per-model `num_ctx` ceiling that's actually pushed on chat calls. v0.2.
- GPU power-cap controls. Not in scope at all (Bloxperts-internal; out of public release).
- vLLM start/stop. Not in scope at all.
- Sudo / root operations of any kind. Not in scope at all.
- `keep_alive` defaults that auto-apply on chat calls. v0.2.
- Multi-Ollama topology (talking to two Ollamas at once). v0.2 at earliest.

## Notes

- DG-004 binding: this page reaches Ollama via the `LLMChat` port (extended with `pull_model` and `delete_model`). Functional spec carries the DG-004 block with the extra methods.
- `keep_alive` and `num_ctx` are surfaced as **read-only** in v0.1 deliberately: pushing them to Ollama on every chat call is the v0.2 "Model Lifecycle" story and depends on a careful per-model policy table that we don't want to design under deadline pressure.
- The audit log is one of two main DP-002 (debuggability) artefacts (the other is JSONL backend logs).
