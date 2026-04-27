# LLM Cockpit — Components

**Status:** Draft **Version:** 0.1

## Component map

```
┌─ Browser (LAN client) ───────────────────────────────────────┐
│  Next.js / React + shadcn UI                                  │
│  Pages: /login  /dashboard  /chat  /code  /settings           │
│  State: TanStack Query for API calls, Zustand for UI state    │
│  Streaming: Server-Sent Events for chat tokens                │
└──────────────────────────────────▲────────────────────────────┘
                                   │ HTTP (LAN-only)
                                   │ Bearer JWT after /auth/login
                                   ▼
┌─ Neuroforge :8080 ───────────────────────────────────────────┐
│  FastAPI backend (Python 3.12)                                │
│                                                               │
│  Routes:                                                      │
│    /api/auth/*       login, logout, me                        │
│    /api/dashboard/*  metrics, models, scheduler stats         │
│    /api/chat/*       create conversation, list, stream        │
│    /api/code/*       same as chat but model=qwen3-coder       │
│    /api/admin/*      pin/unpin model, set num_ctx (admin)     │
│                                                               │
│  Auth: bcrypt password file → JWT (HS256) → Authorization     │
│        header on every API call. JWT expires 7d, refresh OK.  │
│                                                               │
│  Storage: SQLite at /data/cockpit.db                          │
│    users(id, username, pw_hash, role)                         │
│    conversations(id, user_id, model, created_at)              │
│    messages(id, conversation_id, role, content, usage_in,     │
│             usage_out, latency_ms, ts)                        │
│    metrics_snapshot(ts, gpu0_temp, gpu0_pwr, gpu0_mem,        │
│                     gpu1_temp, gpu1_pwr, gpu1_mem)            │
│                                                               │
│  Background jobs:                                             │
│    - 5 s sampler: nvidia-smi → metrics_snapshot               │
│    - 60 s sampler: scheduler /stats → in-memory ring          │
│                                                               │
│  Outbound:                                                    │
│    - HTTP to scheduler at 127.0.0.1:8001                      │
│    - HTTP to Ollama at 127.0.0.1:11434 (admin only)           │
│    - subprocess(nvidia-smi)                                   │
└──────────────────────────────────▲────────────────────────────┘
                                   │
                                   ▼
                       Existing scheduler (port 8001)
                       Existing Ollama          (port 11434)
                       Existing vLLM unit       (port 8000 when active)
```

## Module breakdown — backend

ModuleResponsibility`app/main.py`FastAPI app, lifecycle hooks, CORS for the LAN`app/config.py`Pydantic settings — env-var driven`app/auth.py`bcrypt + JWT issuance / verification, FastAPI dependency `current_userapp/db.py`SQLite engine, session, migrations`app/models.py`SQLAlchemy ORM: `User`, `Conversation`, `Message`, `MetricSnapshotapp/schemas.py`Pydantic request / response shapes`app/routers/auth.py/api/authapp/routers/dashboard.py/api/dashboard` — combined view of telemetry + scheduler stats`app/routers/chat.py/api/chat` — streaming via SSE; routes to scheduler`app/routers/code.py/api/code` — same as chat, model fixed to qwen3-coder`app/routers/admin.py/api/admin` — pin/unpin/keep_alive controls (role=admin only)`app/scheduler_client.py`Thin HTTP client to scheduler:8001`app/ollama_client.py`Thin HTTP client to Ollama:11434 (admin-only operations)`app/telemetry.py`nvidia-smi sampler, runs in `BackgroundTasks` on app startup

## Module breakdown — frontend

PathPurpose`app/(auth)/login/page.tsx`Username + password form, on success stores JWT in HTTP-only cookie`app/(app)/layout.tsx`Authed layout with sidebar (Dashboard / Chat / Code / Admin)`app/(app)/dashboard/page.tsx`Live dashboard — Server Components for SSR + Client Components for charts`app/(app)/chat/page.tsx`Chat UI (Claude-shaped)`app/(app)/code/page.tsx`Code UI (Claude-shaped, code blocks emphasised)`app/(app)/admin/page.tsx`Admin controls, only visible to role=admin`lib/api.ts`Typed fetcher for backend`lib/sse.ts`EventSource wrapper for streaming chat`components/ui/*`shadcn components`components/chart/*`Recharts components for dashboard

## Auth model

- **5 hard-coded user slots** (chris, mila, lex-person, tester1, tester2). Provisioned at first run from a `INITIAL_USERS` env var (semicolon-separated `user:pwhash:role`).
- **Roles**: `admin` (chris) and `user` (others). Admin sees the admin route; users only see chat / code / dashboard (read-only).
- **No registration UI in v0.1.** New users added via `cockpit-admin` CLI script that ships with the backend.

## Streaming

- Chat replies stream via SSE (`text/event-stream`). Frontend uses native `EventSource`. The backend's `/api/chat/stream` connects to the scheduler's `/v1/generate` with `stream: true` and re-emits chunks as SSE events.
- A heartbeat ping every 15 s prevents proxy timeouts.

## Persistence model

- All conversations are persisted. No per-conversation TTL in v0.1.
- SQLite database is one file in a volume mounted from the host (so it survives container restarts).
- Backups: nightly `pg_dump`-style copy to `~/cockpit-backups/`. Out of scope for v0.1; manual.

## Telemetry

- **Sampling cadence** (admin-tunable, default values):
  - GPU metrics: every 5 s.
  - Scheduler stats: every 60 s.
  - Ollama `/api/ps` (loaded models + size_vram): every 30 s.
- **Retention:** rolling 7 days at full resolution; older down-sampled to 1-min buckets.
- **Storage:** `metrics_snapshot` table.

## Integration with the queue layer

The cockpit is the **largest non-agent client** of the queue layer. It must respect:

- The scheduler's `HEAVY_SLOT` semaphore (single-flight reasoning) — when an admin requests `qwen2.5-72B-AWQ` or `deepseek-r1:32b` from chat, the request goes through `HEAVY_SLOT` and may queue.
- The `num_ctx` cap — chat input is rejected at the backend if the prompt would exceed the model's configured `num_ctx` ceiling, with a clear user message.

## Reliability

- The cockpit is **non-critical infrastructure**. Its outage does not affect agentic-blox. Reverse not always true: agentic-blox outage means scheduler outage means cockpit shows "no models reachable".
- Backend runs as a systemd-user service alongside `llm-scheduler`.
- Frontend served by the FastAPI backend as static files (no separate Node process). Build artifact: `frontend/.next` generated at deploy time.

## Out of scope for v0.1

- Per-call cost tracking (planned v0.2).
- Conversation export to vault as markdown (planned v0.2).
- Inline MCP tool calling (planned v0.3).
- Public Internet exposure beyond Tailscale.
- WebSocket-based bidirectional protocol (SSE is fine for v0.1).
