<!-- Status: Review | Version: 1.0 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# LLM Cockpit — Architecture (Components)

**Status:** Review (Sprint 1 architecture sprint)
**Version:** 1.0
**Date:** 2026-04-27

The cockpit is a single Python process that serves a bundled Next.js static frontend, talks to one Ollama daemon, optionally samples `nvidia-smi`, and stores everything in one SQLite file. ADR-002 v1.1 + ADR-003 + ADR-004 are the governing decisions. DP-007 (simplicity) and DP-029 (hexagonal) are the binding architectural principles.

---

## 1. Component map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Browser (LAN client)                            │
│                                                                              │
│  Next.js static export (bundled into the Python wheel)                       │
│  Pages:  /login  /change-password  /dashboard  /chat  /code                  │
│          /admin/users  /admin/ollama                                         │
│  State:  TanStack Query (server state) + Zustand (UI state)                  │
│  Stream: native EventSource for /api/*/stream                                │
└──────────────────────────────────────▲───────────────────────────────────────┘
                                       │ HTTP (LAN)  + Bearer JWT cookie
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       Cockpit backend (FastAPI on :8080)                     │
│                                                                              │
│  ┌─ HTTP layer ────────────────────────────────────────────────────────────┐ │
│  │  routers/ : auth, dashboard, chat, code, admin_users, admin_ollama      │ │
│  │             ├ Depends(current_user)                                     │ │
│  │             ├ Depends(current_user_must_be_settled)   (US-09)           │ │
│  │             └ Depends(require_role(min_role))         (ADR-004)         │ │
│  │  Static  : StaticFiles mount of frontend_dist/ for non-/api paths       │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─ Application core ──────────────────────────────────────────────────────┐ │
│  │  services/  : users, model_tags, metrics, audit, settings               │ │
│  │  ports/     : LLMChat, Telemetry                                        │ │
│  │                                                                         │ │
│  │  No service or router imports an adapter directly.                      │ │
│  │  Dependency injection wires ports → adapters at startup.                │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─ Adapters (the only boundary-crossing code) ────────────────────────────┐ │
│  │  adapters/ollama_chat.py  → OllamaLLMChat       (US-07; binds DG-004)   │ │
│  │  adapters/telemetry.py    → NvidiaSmiTelemetry  (US-02; optional)       │ │
│  │  adapters/fake_chat.py    → FakeLLMChat         (test seam only)        │ │
│  │  adapters/fake_telemetry  → FakeTelemetry       (test seam only)        │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─ Persistence ───────────────────────────────────────────────────────────┐ │
│  │  SQLAlchemy 2 + Alembic migrations against SQLite                       │ │
│  │  $COCKPIT_DATA_DIR/cockpit.db                                           │ │
│  │  Tables: users, conversations, messages, login_audit, admin_audit,      │ │
│  │          model_tags, settings, metrics_snapshot                         │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─ Background jobs (FastAPI lifespan) ────────────────────────────────────┐ │
│  │  telemetry sampler   : 5 s interval if nvidia-smi present               │ │
│  │  model-tag refresh   : 5 min interval; calls LLMChat.list_models()      │ │
│  │  log writer          : JSONL appender for cockpit.log (DP-002)          │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└────────────────┬──────────────────────────────────────┬──────────────────────┘
                 │                                      │
                 ▼ HTTP outbound                        ▼ subprocess (optional)
        ┌────────────────┐                       ┌────────────────┐
        │ Ollama daemon  │                       │ nvidia-smi     │
        │ default :11434 │                       │ on PATH or not │
        └────────────────┘                       └────────────────┘
```

## 2. The two ports

### 2.1 `LLMChat` (US-07)

The cockpit's main outbound port. Methods: `list_models`, `loaded`, `chat_stream`, `pull_model`, `delete_model`. The full surface is in US-07's Functional Spec.

The chat router (US-04), code router (US-05), dashboard router (US-02), and admin Ollama router (US-10) all depend on this port. There is **one** adapter in v0.1: `OllamaLLMChat`. The cockpit knows nothing else about Ollama's wire format outside `app/adapters/ollama_chat.py`.

### 2.2 `Telemetry` (US-02)

Optional outbound port. One method: `sample() -> GpuSnapshot | None`. `None` means "no telemetry available" — the dashboard renders the empty state.

One adapter in v0.1: `NvidiaSmiTelemetry`. If `nvidia-smi` is not on PATH, the adapter is still constructed but `sample()` returns `None`. The cockpit does not refuse to start over GPU absence.

## 3. Authorization

Three roles on a ladder: `chat < code < admin` (ADR-004). One column on `users`. The `require_role(min_role)` dependency is the **only** authorization gate.

JWT carries `sub` (user id) only. Role is resolved from `users` at every request. Role flips by an admin (US-06) take effect immediately on the user's next request; no re-login required (ADR-004 §5).

## 4. Data model

```sql
-- Users + auth
users           (id, username, pw_hash, role, must_change_password,
                 password_changed_at, created_at, last_login_at, deleted_at)

-- Sessions audit
login_audit     (id, ts, username, success, source_ip, action)         -- 'login', 'logout', 'password_changed'

-- Conversations + messages
conversations   (id, user_id, mode, model, title, system_prompt, created_at, updated_at)
                                  -- mode IN ('chat', 'code')
messages        (id, conversation_id, role, content, model, usage_in,
                 usage_out, latency_ms, gen_tps, ts, error)

-- Telemetry
metrics_snapshot(ts, gpu0_temp, gpu0_pwr, gpu0_mem, gpu1_temp, gpu1_pwr, gpu1_mem)
                                  -- nullable columns; NULL when nvidia-smi absent

-- Model metadata
model_tags      (model PK, tag, source, updated_at)
settings        (key PK, value, updated_at)

-- Admin audit
admin_audit     (id, ts, actor_user_id, action, target_user_id, target_model, details_json)
```

DP-013 (memory write boundaries):

- `users` / `login_audit` written by `routers/auth.py` and `services/users.py`.
- `conversations` / `messages` written by `routers/chat.py` and `routers/code.py`.
- `metrics_snapshot` written by the telemetry sampler background job.
- `model_tags` / `settings` written by `routers/admin_ollama.py` and `services/model_tags.py`.
- `admin_audit` written by any admin write — `routers/admin_users.py` and `routers/admin_ollama.py`. Read by the audit panel.

No router writes outside its assigned tables.

## 5. Distribution and startup

Per ADR-002 v1.1:

- The Python wheel embeds `frontend_dist/` (built Next.js static export).
- `cockpit-admin` is the one CLI: `init`, `serve`, `migrate`, `user-*`, `doctor`, `systemd-install`.
- `init` (US-08) is idempotent and probes Ollama before doing any other work.
- `serve` runs `alembic upgrade head` on startup, then starts FastAPI; serves the embedded frontend at `/`, the API at `/api/*`.

A second process / container is **not** required.

## 6. Configuration resolution

```
COCKPIT_OLLAMA_URL        env       (highest precedence)
[ollama] url              config.toml
OLLAMA_HOST               env       (Ollama's own convention)
http://127.0.0.1:11434    default
```

Same precedence pattern for `COCKPIT_DATA_DIR`, `COCKPIT_HOST`, `COCKPIT_PORT`, `COCKPIT_JWT_SECRET`.

## 7. Deployment shapes

| Shape | When to use | Notes |
|-------|-------------|-------|
| `pip install` + `cockpit-admin serve` | dev, single-host home use | foreground; logs to stdout |
| `pip install` + `cockpit-admin systemd-install` | Linux home / lab server | `~/.config/systemd/user/llm-cockpit.service`; `--user` so no root |
| Docker Compose | cross-platform "I just want it running" | image bundles Python + frontend; SQLite in named volume |
| `pipx install llm-cockpit` | dev tooling style | clean `~/.local/bin` install |

Public-Internet exposure is not in this project's scope. Reverse-proxy at the operator's discretion.

## 8. What this architecture deliberately does **not** include

- **No upstream queue layer** in v0.1 (ADR-003 §4). If a deployment needs queue semantics for heavy GPU work, that is solved outside the cockpit — e.g. by AgenticBlox proxying Ollama and the cockpit pointing `COCKPIT_OLLAMA_URL` at the proxy.
- **No model lifecycle controls in v0.1** (`pin`, `keep_alive`, `num_ctx` push). Defer to v0.2 ("Model Lifecycle"). v0.1 admin scope is user management (US-06) plus tagging / pull / delete (US-10).
- **No external identity provider.** OAuth / SAML / OIDC are not in v0.1. Bcrypt + JWT in `HttpOnly` cookie is the floor.
- **No multi-host topology.** One cockpit, one Ollama, one SQLite.
- **No password reset over email.** Admin reset is the only path in v0.1.
- **No server-rendered React.** The frontend is a static export; we lose RSC for v0.1 in exchange for a one-process distribution. Revisit if SSR becomes load-bearing.

## 9. Open architecture questions for end-of-Sprint-1 review

1. **JWT vs server-side session.** JWT is current default (ADR-002 §Auth). A server-side session table would let us invalidate sessions instantly on role change / delete. Trade: more state, simpler invalidation. **Tentative answer:** keep JWT; role is resolved per-request anyway (ADR-004 §5), and delete sets `deleted_at` so the next request 401s.
2. **`messages` row growth.** A heavy chat user at 32 k context could write millions of rows over time. Single SQLite file → eventual VACUUM cost. **Tentative answer:** v0.1 ships without retention policy; documented as a known limitation. v0.2 adds a configurable retention (e.g. 90 days for messages).
3. **CSRF.** Cookie-based JWT + same-origin SPA: do we need CSRF tokens? **Tentative answer:** `SameSite=Strict` covers the common cases for v0.1. Revisit if anyone runs the cockpit behind a reverse proxy on a different origin.
4. **Frontend bundle size.** Next.js static export of a 7-page app is a few MB; the wheel grows. **Tentative answer:** acceptable; lazy-load admin and code pages.
5. **Telemetry on Apple Silicon and AMD.** `nvidia-smi` covers NVIDIA only. Mac users get the empty state. Should we add `powermetrics` (Mac) and `rocm-smi` (AMD)? **Tentative answer:** v0.2. Public release ships with NVIDIA + empty state; everyone else sees an honest "no telemetry" panel.

These five questions are **the** architecture-sprint review items. Resolution before Sprint 2 starts.

## Revision history

- **v1.0 (2026-04-27)** — Rewritten for the public-release framing (ADR-003) and role ladder (ADR-004). Scheduler client removed. Telemetry made optional. Distribution moved to pip-installable wheel with bundled frontend. Two ports (`LLMChat`, `Telemetry`); five open questions for Sprint 1 review.
- **v0.1 (2026-04-26)** — Initial Draft, internal Neuroforge framing.
