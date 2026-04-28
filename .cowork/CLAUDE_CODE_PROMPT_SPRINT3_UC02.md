# Claude Code prompt — Sprint 3: UC-02 Live dashboard + placement board

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

PRs #3 (UC-08 Slice B), #4 (UC-01), #5 (UC-09) are merged into `develop`.
`develop` now has the full Sprint 2 stack: install → serve → login → change-password → placeholder dashboard.

## Read first (before writing a single line)

1. `CLAUDE.md` — rules, Spec-First gate, branch/commit conventions.
2. `docs/process/SPRINT_STATE.md` — confirm Sprint 3 is open.
3. `docs/specs/functional/UC-02-dashboard-live.md` — **Accepted**. This is your primary reference.
4. `docs/specs/test/UC-02-dashboard-live.md` — currently **Draft stub**. You will fill it in as the first commit (see §Step 0 below).
5. `docs/architecture/COMPONENTS.md` — for port / adapter topology.

---

## Branch

```
git checkout develop && git pull
git checkout -b feature/UC-02-dashboard
```

Commit prefix: `[UC-02]`. One PR against `develop` when done.

---

## What already exists — do not rebuild

- `src/cockpit/ports/llm_chat.py` — `LLMChat` Protocol + all exceptions.
- `src/cockpit/adapters/ollama_chat.py` — `OllamaLLMChat` (all five methods).
- `src/cockpit/adapters/fake_chat.py` — `FakeLLMChat` + `last_call` recorder.
- `src/cockpit/models.py` — `users`, `login_audit`, `model_tags`, `settings`, `model_config`, `model_perf`. Missing: `admin_audit`, `metrics_snapshot` — add these (see §Migration).
- `src/cockpit/main.py` — `create_app()`, lifespan, `StaticFiles` mount.
- `src/cockpit/deps.py` — `get_session`, `get_settings`.
- `src/cockpit/routers/auth.py` — full auth surface.
- `src/cockpit/services/users.py`, `bootstrap.py`, `model_tags.py` — complete.

---

## Build order — follow this sequence

### Step 0 — Fill in the test spec (first commit, no implementation yet)

`docs/specs/test/UC-02-dashboard-live.md` is a Draft stub. Fill in:
- **Approach** section: two test surfaces (unit via FakeLLMChat + FakeTelemetry; integration via `pytest -m integration` against real Ollama — not required for develop merge).
- **Test cases** covering every AC in the functional spec.
- **Pass criteria**: ≥ 90 % coverage on `cockpit/ports/telemetry.py`, `cockpit/adapters/telemetry.py`, `cockpit/routers/dashboard.py`, `cockpit/routers/admin_ollama.py`, `cockpit/services/metrics.py`.

Flip the test spec header to `Status: Accepted`. Commit: `[UC-02] fill in test spec (v0.1 → Accepted)`.

Do not write any implementation code in this commit. Chris's acceptance of this commit is implied since he authorised the "build to MVP" run — proceed to Step 1 immediately.

### Step 1 — Telemetry port + adapters

**`src/cockpit/ports/telemetry.py`**:

```python
@dataclass(frozen=True)
class GpuSnapshot:
    index: int
    vram_used_mb: int
    vram_total_mb: int
    temp_c: float | None
    power_w: float | None

class Telemetry(Protocol):
    async def sample(self) -> list[GpuSnapshot] | None:
        """Return GPU snapshots, or None when telemetry is unavailable."""
        ...
```

Exceptions on the port:
- `TelemetryUnavailableError` — `nvidia-smi` not found or returns non-zero.

**`src/cockpit/adapters/telemetry.py`** — `NvidiaSmiTelemetry`:
- Runs `nvidia-smi --query-gpu=index,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits` as an async subprocess.
- Returns `None` (not an error) when the binary isn't found — GPU is optional per ADR-003 §5.
- Raises `TelemetryUnavailableError` on non-zero exit with available binary.
- Parse the CSV output; `temp_c` and `power_w` are `None` if the column is `[N/A]`.

**`src/cockpit/adapters/fake_telemetry.py`** — `FakeTelemetry`:
- Configurable static `list[GpuSnapshot] | None` return.
- Records each `sample()` call in `last_call` (mirrors `FakeLLMChat` shape for consistency).

Tests: `tests/test_uc02_telemetry.py`. Cover happy path parsing, `[N/A]` columns, binary-not-found returns `None`, non-zero exit raises `TelemetryUnavailableError`. Use `unittest.mock.AsyncMock` / `subprocess` mocking — no real GPU required.

### Step 2 — Database migration (models + Alembic)

Add to `src/cockpit/models.py`:

```python
class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, ...)
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w: Mapped[float | None] = mapped_column(Float, nullable=True)

class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, ...)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_model: Mapped[str | None] = mapped_column(String, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String, nullable=True)
```

Create `src/cockpit/migrations/versions/0002_dashboard.py` (Alembic revision). Both tables. Run `alembic upgrade head` to verify locally before committing.

### Step 3 — Services

**`src/cockpit/services/metrics.py`**:

- `GpuSampler` — runs every 5 s; calls `Telemetry.sample()`; inserts `MetricsSnapshot` rows. Implemented as an `asyncio.Task` started in `main.py`'s lifespan.
- `ModelStateSampler` — runs every 30 s; calls `LLMChat.loaded()` and `LLMChat.list_models()`; holds results in an in-memory `asyncio.Lock`-protected dict used by the snapshot endpoint.
- `get_dashboard_snapshot(session, model_state, gpu_snapshots, last_n_calls)` — assembles the `/api/dashboard/snapshot` payload (pure function, easy to test).

**`src/cockpit/services/audit.py`**:
- `write_admin_audit(session, *, actor_id, action, target_model, details, source_ip)` — inserts an `AdminAudit` row. Called by placement, perf-test, pull, delete, settings-patch.

**Per-model `asyncio.Lock` (ADR-005 §5)**:
- `model_locks: dict[str, asyncio.Lock]` on `app.state`. Keyed by model name. Created on first access (defaultdict pattern). Used by placement warm-up and perf harness to enforce single-flight.
- `host_perf_lock: asyncio.Lock` on `app.state` — ensures only one perf test runs at a time across all models.

Wire `GpuSampler`, `ModelStateSampler`, and `model_locks` / `host_perf_lock` into `main.py`'s lifespan. Pass `chat_factory` and `telemetry_factory` as DI seams (mirrors the existing `chat_factory` pattern).

### Step 4 — Dashboard router

**`src/cockpit/routers/dashboard.py`**:

```
GET /api/dashboard/snapshot   → Depends(current_user_must_be_settled)  → DashboardSnapshot
GET /api/dashboard/stream     → Depends(current_user_must_be_settled)  → SSE
```

- `snapshot` reads from `app.state.model_state` (in-memory) and `app.state.last_gpu_snapshots` plus `session` for `last_calls` (last 20 rows from `messages` — that table doesn't exist yet; return `[]` for now with a `# TODO UC-04` comment).
- `stream` uses `sse_starlette.sse.EventSourceResponse`; yields the snapshot payload every 5 s.
- Shape must match the JSON schema in the functional spec exactly. Write a Pydantic schema in `schemas.py` for it and validate in tests.

Register under prefix `/api/dashboard` in `main.py`.

### Step 5 — Admin Ollama router

**`src/cockpit/routers/admin_ollama.py`**:

All endpoints require `Depends(require_role("admin"))`.

```
POST   /api/admin/ollama/models/{model}/place       → PlaceResponse
POST   /api/admin/ollama/models/{model}/perf-test   → SSE
POST   /api/admin/ollama/models/{model}/pull        → SSE
DELETE /api/admin/ollama/models/{model}             → 204
PATCH  /api/admin/ollama/models/{model}/settings    → 200
```

**Placement logic** (per spec §Backend logic, placement transition):
1. Validate `placement` against `["gpu0".."gpu{n}", "multi_gpu", "on_demand", "available"]`. Reject unknown values → 422.
2. `UPDATE model_config SET placement = ?` (upsert).
3. Compute options per the spec table (keep_alive, main_gpu, num_gpu).
4. Acquire `model_locks[model]` (asyncio.Lock — single-flight).
5. Issue a one-token warm-up via `LLMChat.chat_stream(model, [{"role":"user","content":" "}], options={...})`. Discard output.
6. Wait up to 10 s for `LLMChat.loaded()` to confirm (or absence for on_demand/available).
7. Compare requested vs. actual GPU via `Telemetry.sample()` before/after. If GPU count mismatch, set `mismatch=true`.
8. Write `AdminAudit` row.
9. Return `PlaceResponse(applied={...}, loaded_now=bool)`.

**Perf harness** (per spec §Performance harness) — implement the outline from the functional spec exactly. Emit SSE stage events. Use `host_perf_lock` to enforce one-at-a-time across models. Save result to `model_perf`. Restore prior placement on completion.

**Pull** (`POST /pull`) — stream `LLMChat.pull_model()` as SSE `PullProgress` events.

**Delete** (`DELETE`) — call `LLMChat.delete_model()`, delete `model_config` row if present, write audit. Return 204.

**Settings patch** (`PATCH /settings`) — update `model_config` columns: `keep_alive_seconds`, `num_ctx_default`, `single_flight`, `notes`. Write audit. Return 200.

Register under prefix `/api/admin/ollama` in `main.py`.

### Step 6 — Frontend dashboard placeholder upgrade

Replace `src/cockpit/frontend_dist/dashboard/index.html` with a functional read-only dashboard:

- On load: `GET /api/dashboard/snapshot` then `EventSource('/api/dashboard/stream')` for live updates.
- Render the placement columns (GPU 0..N, Multi-GPU, On Demand, Available) as HTML sections.
- Each model card shows: name, tag, size, placement, loaded status, last perf metrics if available.
- GPU strip header: show `vram_used_mb / vram_total_mb` per GPU.
- Admin controls (placement): render `<select>` per model card (only for `role === 'admin'`) that POSTs to `/place` on change. No drag-and-drop yet — that is a Next.js / Sprint 4 concern.
- Pull and delete: admin-only buttons per card.
- "Test performance" button: opens a `<dialog>` and streams SSE from `/perf-test`, shows stage progress.

Plain HTML + inline vanilla JS. Keep it functional, not pretty. The real React/dnd-kit frontend lands when the Next.js build is wired (Sprint 4).

---

## Spec status edits (vault not mounted — fallback rule)

- Functional spec: flip `Accepted → In Progress` at branch open, `Done (technical)` when tests pass.
- Test spec: already flipped to `Accepted` in Step 0.
- Add `<!-- VAULT-SYNC -->` comments for sprint review mirroring.

---

## Coverage target

```
pytest --cov=cockpit.ports.telemetry \
       --cov=cockpit.adapters.telemetry \
       --cov=cockpit.routers.dashboard \
       --cov=cockpit.routers.admin_ollama \
       --cov=cockpit.services.metrics \
       --cov=cockpit.services.audit \
       --cov-report=term-missing
```

≥ 90 % on each module. All prior tests (135 + deferred skip) must stay green.

---

## Stop and ask Chris if

- The `messages` table doesn't exist yet (UC-04) — `last_calls` in the snapshot should return `[]` with a `# TODO UC-04` comment, not block.
- `nvidia-smi` GPU count affects the valid placement column list — default to `["gpu0", "multi_gpu", "on_demand", "available"]` when no GPU is detected.
- The perf harness `probe_max_context` logic isn't fully specified in the functional spec — implement a binary search over the `contexts` list (probe the largest, if it fails try the next, etc.) and document the assumption.
- Any Ollama API behaviour differs from what the spec assumes — document the gap and ask before adapting.
