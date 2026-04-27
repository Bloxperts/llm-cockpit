<!-- Status: Draft | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# US-06 · Functional Spec — Admin controls

**Status:** Draft
**Depends on:** US-01 (login), US-07 (scheduler routing).
**User Spec:** [`../user/US-06-admin-controls.md`](../user/US-06-admin-controls.md)
**Test Spec:** [`../test/US-06-admin-controls.md`](../test/US-06-admin-controls.md)
**Bound DG:** DG-004 — see block at end of file.

## Goal

Replace the "edit systemd unit + ssh restart" workflow for the most common operational changes with a UI panel. Only `role=admin` (Chris) sees this page.

## Panel layout

1. **Pinned models list** — current pins (gemma4:26b, qwen3-coder:30b, embeddinggemma:300m). For each: keep_alive, num_ctx, "unload" button.
2. **Available models** — `ollama list` output, with "load with..." button that pops a small dialog asking for `num_ctx` and `keep_alive` defaults.
3. **Per-agent num_ctx ceiling** — table of agent role → max num_ctx allowed. Editing a row updates the queue layer's policy and kicks reloads of the chat page's model picker.
4. **Power-cap controls** — sliders for GPU 0 (default 320 W) and GPU 1 (default 350 W). Changing emits a `sudo nvidia-smi -pl` via a privileged backend route.
5. **vLLM control** — buttons "Start vLLM 72B (TP=2)" and "Stop vLLM". Wraps `systemctl --user start/stop vllm-qwen72b`.

## API

```
GET  /api/admin/models                    → loaded + available
POST /api/admin/models/{name}/pin         body { num_ctx, keep_alive }
POST /api/admin/models/{name}/unpin       → 204
GET  /api/admin/policy/num_ctx_per_agent  → { lex: 32768, kai: 32768, ... }
PATCH /api/admin/policy/num_ctx_per_agent body { lex: 65536 }
POST /api/admin/power-limit               body { gpu: 0, watts: 320 }
POST /api/admin/vllm/start
POST /api/admin/vllm/stop
GET  /api/admin/vllm/status               → { active, since, vram_used }
```

The power-limit and vLLM endpoints require the backend to invoke shell commands. They run as the `bloxperts` user and rely on sudoers (NOPASSWD scoped to specific commands — *not* full NOPASSWD). Sudoers fragment for the cockpit:

```
# /etc/sudoers.d/llm-cockpit
bloxperts ALL=(root) NOPASSWD: /usr/bin/nvidia-smi -pl *
bloxperts ALL=(root) NOPASSWD: /bin/systemctl start ollama
bloxperts ALL=(root) NOPASSWD: /bin/systemctl stop ollama
bloxperts ALL=(root) NOPASSWD: /bin/systemctl start ollama-warmup
```

## Audit

Every admin action writes a row to `admin_audit (ts, user, action, payload)`.

## Acceptance criteria

- ✅ Non-admin user cannot reach `/admin` — redirected to `/`.
- ✅ Pinning a new model loads it and `Ollama /api/ps` shows it within 30 s.
- ✅ Unpinning removes from VRAM within 30 s of `keep_alive` expiry.
- ✅ Setting GPU 0 power cap to 280 W → `nvidia-smi --query-gpu=power.limit --format=csv` confirms within 5 s.
- ✅ Starting vLLM stops Ollama, runs vLLM, and the cockpit's chat page now lists `qwen2.5-72B-AWQ` as a model option.

---

## DG-004 output block · port or adapter

**Crosses the platform boundary?** Yes. Admin actions reach into four privileged surfaces.

| External system | Direction | Port (core) | Adapter (concrete) | Inbound / Outbound |
|---|---|---|---|---|
| Ollama daemon (`/api/pull`, `/api/ps`, `/api/delete`, keep_alive ping) | Write | `ModelLifecycle` (`load`, `unload`, `set_keep_alive`, `set_num_ctx`) | `OllamaModelLifecycle` in `app/ollama_client.py` | Outbound |
| Scheduler service (`/policy/num_ctx_per_agent` PATCH) | Write | `SchedulerControl.policy_*` | `SchedulerHTTP` in `app/scheduler_client.py` | Outbound |
| Host shell (`sudo nvidia-smi -pl …`) | Write | `PowerControl` (`set_power_limit(gpu, watts)`) | `SudoPowerControl` (subprocess + sudoers) | Outbound |
| `systemctl --user start/stop vllm-qwen72b` | Write | `ServiceControl` (`start`, `stop`, `status`) | `SystemdUserServiceControl` (subprocess) | Outbound |

**Why each is a port, not a direct call:**

- The admin router must be unit-testable without a GPU and without root. Each adapter has an in-memory fake.
- Sudoers fragment is documented inline in this Functional Spec — the *adapter* is the only place that knows about the shell-out.
- vLLM lifecycle is fully behind `ServiceControl`: the dashboard / chat / code routers should not need to know whether the backend is Ollama or vLLM today (DP-008 escape-hatch).

**Inbound side:** none. All admin actions are user-initiated via the `/api/admin/*` HTTP routes; there is no inbound port.

**Risk callout:** `PowerControl` and `ServiceControl` shell out as `bloxperts` with NOPASSWD scoped to specific commands. This is the only place in the cockpit that escalates privilege; the Functional Spec audit panel and `admin_audit` table cover DP-002 (debuggability).

**Compliance:** DP-029 satisfied for all four boundaries; DP-014 (Governance & Budget Contracts) explicitly applies to `ModelLifecycle` and `PowerControl`; DP-031 (Progressive Autonomy) is realised by gating the entire admin router on `role=admin`.

