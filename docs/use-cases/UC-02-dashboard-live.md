<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-02 · Use Case — Live dashboard + model placement board

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`../specs/functional/UC-02-dashboard-live.md`](../specs/functional/UC-02-dashboard-live.md)
**Test Spec:** [`../specs/test/UC-02-dashboard-live.md`](../specs/test/UC-02-dashboard-live.md)
**Sprint:** 3
**Related:** UC-07 (Ollama integration via `LLMChat`), ADR-003 (telemetry optional), ADR-004 (role gate; placement is admin-only), **ADR-005 (per-model lifecycle + perf harness in v0.1)**.
**Min role:** any logged-in user (read-only board); drag-drop and "+ Add model" require `admin`.

## Story

> As an `admin` I want a single page where I can see how Ollama is using the host's GPU(s), drag models between GPU zones / On-Demand / Available, install new models, and at-a-glance see each model's measured performance, so that I can shape the runtime for the way users actually use it without ever opening a terminal.
>
> As a `chat` or `code` user I want to see what models are currently available, where they're loaded, and how the host is doing, so that I can pick a model intelligently and notice when something is off.

## Target state

`/dashboard` is a single page in three vertical bands:

1. **Header strip.** Current time, "Healthy / Degraded / Ollama unreachable" badge, and a small per-GPU VRAM strip (e.g. `GPU 0: 14.2 / 24.0 GB · GPU 1: 21.7 / 24.0 GB`). When `nvidia-smi` is absent, the GPU strip collapses to "No GPU telemetry".
2. **Model placement board.** The primary visual on this page (more on it below).
3. **Recent calls.** Last 20 calls (admin sees everyone's; `chat` / `code` users see only their own). Live-tail via SSE.

### The placement board

Columns, left to right:

- **GPU 0**, **GPU 1**, … (one per detected GPU). Each column header shows the GPU's VRAM bar.
- **Multi-GPU** (only when ≥ 2 GPUs detected). Models that should span all GPUs (`num_gpu = 99`).
- **On Demand**. Models with `keep_alive=0`; load per call, drop after.
- **Available**. Models installed in Ollama but parked — neither warm nor on-demand. Drag here to drop a model from a GPU but keep it installed.

Each card represents one model. The card body shows:

- Model name + tag chip (`chat` / `code` / `both`, per ADR-004).
- VRAM size (current actual when loaded; on-disk size otherwise).
- Compact metrics, computed from the most recent `model_perf` row: cold-load time, throughput tokens-per-second, max context observed. If no perf data exists, the card shows a "Run perf test" call-to-action where the metrics would go.
- A small **"requested vs. actual"** indicator if Ollama placed the model differently than the column suggests (e.g. requested `GPU 0` but VRAM growth was on `GPU 1` because `GPU 0` was full). This is a chip with a tooltip — "Requested GPU 0 · Ollama placed on GPU 1 (insufficient VRAM)".

Card actions (admin-only menu on hover):

- **Test performance** — runs the harness defined in ADR-005 §4 (cold load + throughput + max-ctx probe). Disruptive, ~30 s. Shown progress in a side drawer.
- **Settings** — opens a small modal: `keep_alive_seconds` override, `num_ctx_default`, `single_flight` toggle, free-text notes.
- **Delete** — removes the model from Ollama (`LLMChat.delete_model`). Confirmation modal because the on-disk weights go away.

Interactions:

- **Drag-and-drop (admin only).** Drag a card from any column to any other column. The cockpit updates `model_config.placement`, calls Ollama with the appropriate `keep_alive` / `main_gpu` / `num_gpu` options, and within ~10 s the card settles in its new column with the actual placement reflected on the chip.
- **Click a card (any user).** Opens a side drawer with full metrics history (perf-test rows, recent calls for that model), conversation count, and — for admins — the same Settings panel inline.
- **"+ Add model" button** at the top-right of the board (admin only). Side drawer prompts for a model name, optional link to https://ollama.com/library for browsing. Pull progress streams in the drawer; on completion the new model lands on the `Available` column.

Refresh cadence:

- GPU strip + GPU columns: every 5 s (when telemetry available).
- Loaded-model state: every 30 s (cockpit calls `LLMChat.loaded()`).
- Recent-calls table: live-tail via SSE.
- Card "VRAM actual" + "requested vs actual" chip: re-evaluated on every loaded-model refresh.

## Acceptance criteria

1. The page loads in &lt; 500 ms with a meaningful first paint.
2. The placement board renders one column per detected GPU, plus Multi-GPU (when ≥ 2 GPUs), On Demand, and Available — with no GPU columns at all on a host without `nvidia-smi`.
3. As an admin, dragging a model card from `Available` to `GPU 0` triggers an Ollama load with `keep_alive=24h` and `main_gpu=0`, and the card settles in the GPU 0 column within 10 s on a host where the model fits.
4. If the model does not fit on the requested GPU and Ollama places it elsewhere, the card displays the "requested vs. actual" chip with a useful tooltip — and the card's column position reflects the actual placement.
5. As a non-admin user, the cards on the board are visible but **not draggable**; the action menu is hidden; the "+ Add model" button is hidden.
6. The "Test performance" action runs the cold-load + throughput + max-ctx harness from ADR-005, surfaces progress in a side drawer, and writes one row to `model_perf` on completion.
7. After a perf test, the model is restored to its prior placement (if it was warm somewhere); if it was `on_demand` / `available`, it stays unloaded.
8. "+ Add model" pulls a named model from the Ollama registry, streaming progress, and the model appears in `Available` on completion with a default `model_config` row.
9. The recent-calls panel filters by user role: `chat` / `code` see their own; `admin` sees everyone's. No model contents leak between users.
10. When Ollama becomes unreachable, the header badge flips to "Ollama unreachable" within 30 s; the placement board disables drag-drop and shows a friendly retry hint; existing card data persists (the board does not flicker empty).

## Scope boundaries (out)

- Hard per-GPU pinning over a single Ollama daemon (Ollama doesn't support it; v0.1 ships with the `main_gpu` hint and the "requested vs. actual" honesty chip).
- Multiple Ollama daemons on the same host (one per GPU) — out of scope.
- vLLM as an alternative backend on the same dashboard — v0.2.
- A/B compare two models on the same prompt — v0.2 (UC-A1).
- Cost ledger / per-call cost calculation — v0.2 (UC-CL).
- Mobile / tablet UX optimisation — v0.2 (cards still render, but drag-drop on touch is best-effort in v0.1).

## Notes

- DG-004 binding: this UC consumes `Telemetry` and `LLMChat` (subset). The Functional Spec carries the DG-004 block.
- The placement board is the **primary admin lifecycle surface** in v0.1. UC-10 (admin Ollama config) becomes a smaller page for tagging heuristics, code-mode default system prompt, perf-test history, and the audit log.
- Cards inherit chat/code/both tagging from `model_tags` (ADR-004 §3); the heuristic + override flow lives in UC-10.
- "Requested vs. actual" placement detection works by comparing pre/post-load `nvidia-smi` VRAM deltas with the requested GPU. When `nvidia-smi` is absent the cockpit cannot detect mismatches; the card simply trusts Ollama's placement.
