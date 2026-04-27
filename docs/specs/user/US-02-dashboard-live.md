<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# US-02 · User Spec — Live dashboard

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-02-dashboard-live.md`](../functional/US-02-dashboard-live.md)
**Test Spec:** [`test/US-02-dashboard-live.md`](../test/US-02-dashboard-live.md)
**Sprint:** 3
**Related:** US-07 (Ollama integration — the dashboard reads from the same `LLMChat`-adjacent ports), ADR-003 (telemetry optional), ADR-004 (admin only sees full metrics; chat/code users see a trimmed view).

## Story

> As an `admin` I want a single page that summarises Ollama's current state — what's loaded, recent calls, GPU health if I have a GPU — so that I can spot problems before users do, without SSH'ing into the host.
>
> As a `chat` or `code` user I want a brief dashboard that shows whether the cockpit is alive and which models are available, but I don't need to see system-wide metrics or other people's calls.

## Target state

`/dashboard` shows, top to bottom:

- **Header strip.** Current time, "Cockpit is healthy / degraded / Ollama unreachable" badge.
- **Available models.** Table of every model Ollama is serving (from `/api/tags`), with VRAM if loaded (from `/api/ps`), tag (`chat` / `code` / `both`, per ADR-004), and a "use this model" button that drops the user into the right page (`/chat` or `/code`).
- **GPU panel.** Per-GPU temp / VRAM / power. Sparkline for last 5 min. **Renders empty state ("No GPU telemetry detected") if `nvidia-smi` is not on PATH.**
- **Recent calls** (admin only). Last 20 rows joined from `messages` + `users`: `ts | user | model | prompt_tok | completion_tok | gen_tps | latency_ms`. Live-tail via SSE.
- **My recent calls** (chat / code users). Same table, filtered to their own user id only. Last 20 rows.

Refresh cadence: every 5 s for GPU, every 30 s for models, live-tail (SSE) for calls.

## Acceptance criteria

1. Page loads in &lt; 500 ms with a meaningful first paint.
2. GPU panel renders the empty state when `nvidia-smi` is not on PATH and the rest of the dashboard works.
3. When a GPU is present, temp updates within 5 s of an actual change observed via `nvidia-smi`.
4. When a model is unloaded out-of-band (Ollama eviction), the model row's VRAM column clears within 30 s.
5. When Ollama is unreachable, the header badge flips to "Ollama unreachable" within 30 s and a friendly retry hint is shown.
6. `chat` and `code` users see only their own recent calls in the calls table; `admin` sees everyone's.
7. Health rules (machine-checkable):
   - **Healthy:** Ollama reachable, `nvidia-smi` either absent or all GPUs &lt; 80 °C.
   - **Degraded:** GPU &gt; 85 °C, or any model in `/api/ps` shows `until=null`, or `/api/tags` lists nothing.
   - **Ollama unreachable:** `/api/tags` returns no response or non-2xx for &gt; 30 s.

## Scope boundaries (out)

- Per-GPU thermal / power *control* — that's a v0.2 candidate.
- Cost ledger / per-call cost — v0.2.
- Multi-host aggregation — v0.2.

## Notes

- DG-004 binding: the dashboard consumes `Telemetry` and `ModelInventory` ports. Functional spec carries the DG-004 block.
- "Call" rows that originate before a user existed (legacy) appear with `user="<deleted>"` to preserve audit trail without resurrecting deleted users. (Owned by US-06; mentioned here for completeness.)
