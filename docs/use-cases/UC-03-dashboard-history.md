<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-03 · User Spec — Dashboard history (24 h, 7 d)

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`functional/UC-03-dashboard-history.md`](../specs/functional/UC-03-dashboard-history.md)
**Test Spec:** [`test/UC-03-dashboard-history.md`](../specs/test/UC-03-dashboard-history.md)
**Sprint:** 5
**Depends on:** UC-02 (uses the same metrics tables).

## Story

> As an `admin` I want to see how the cockpit / Ollama have behaved over the last 24 hours and last 7 days so that I can reason about capacity, spot patterns, and explain "why was it slow yesterday" without diving into raw logs.
>
> As a `chat` or `code` user I want to see my own usage history (messages I sent, models I used, total tokens) over the last 24 h / 7 d so that I have a sense of my own consumption.

## Target state

`/dashboard/history` shows two sections:

- **System-wide** (admin only). Stacked bar chart of calls per model per hour for last 24 h and per day for last 7 d. Line chart of GPU temp p50 / p95 over last 24 h (when telemetry available). Top-5 models by total tokens, last 7 d.
- **Per-user** (everyone). Their own messages-sent count by day for last 7 d, total prompt + completion tokens, mean latency.

Time-range toggle: `24 h` / `7 d`. Default: `24 h`.

## Acceptance criteria

1. Page loads in &lt; 1 s with both charts rendered (data already aggregated server-side).
2. `chat` / `code` users see only their own usage panel; `admin` sees system-wide *and* their own panel.
3. When telemetry is absent, the GPU temp line chart renders an empty state with the same "No GPU telemetry detected" message as UC-02.
4. Aggregations are computed in SQLite, not in the browser. The browser receives ready-to-render arrays.
5. The page refreshes its data on demand via a "Refresh" button — no polling. (Historical data does not need live tail.)

## Scope boundaries (out)

- Drill-down into a specific call → message body. v0.2.
- Cost views. v0.2.
- Export to CSV / Markdown. v0.2.

## Notes

- Pure read story, no boundary crossings. **No DG-004 block needed** unless v0.2 introduces an external store.
- Aggregation queries are written as plain SQL views in the migration that creates `messages` / `metrics_snapshot`.
