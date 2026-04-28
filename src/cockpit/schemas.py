"""Pydantic v2 request/response models.

Centralised here so routers and tests share one shape per payload. Keep
schemas thin — they're wire-format types, not domain types. The ORM types
live in `cockpit.models`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)


class MeResponse(BaseModel):
    """Identity payload used by `GET /api/auth/me` and embedded in
    `LoginResponse.user`. The `must_change_password` field tells the frontend
    whether to redirect the user through the UC-09 forced-change flow.

    Sprint 7 added `session_ttl_days` so the frontend's preferences UI
    can show the current preference. `None` = system default (7 days).
    """

    id: int
    username: str
    role: str  # 'chat' | 'code' | 'admin' (ADR-004; enforced at the DB CHECK)
    must_change_password: bool
    session_ttl_days: int | None = None  # 0 / 1 / 7 / 30; None = default (7)


class LoginResponse(BaseModel):
    user: MeResponse
    ttl_seconds: int


class ChangePasswordRequest(BaseModel):
    """UC-09. Server validates length ≥ 8, equality, and != literal 'ollama'."""

    new_password: str
    confirm_password: str


class SessionTtlRequest(BaseModel):
    """Sprint 7 — per-user JWT-lifetime preference.

    Allowed values: 0 (essentially unlimited / 10 years), 1, 7, 30 days.
    The auth router validates against the canonical `TTL_MAP` keys.
    """

    ttl_days: int


class LogoutResponse(BaseModel):
    pass


# --- UC-02: dashboard snapshot --------------------------------------------


class GpuPayload(BaseModel):
    index: int
    vram_used_mb: int
    vram_total_mb: int
    temp_c: float | None
    power_w: float | None
    # Sprint 5b: configured power cap (`nvidia-smi --query-gpu=power.limit`).
    # Default `None` so existing tests / payloads that don't carry the field
    # still validate.
    max_power_w: int | None = None


class ModelConfigPayload(BaseModel):
    placement: str
    keep_alive_seconds: int | None
    num_ctx_default: int | None
    single_flight: bool


class ModelActualPayload(BaseModel):
    loaded: bool
    vram_mb: int | None
    main_gpu_actual: int | None
    mismatch: bool


class ModelMetricsPayload(BaseModel):
    cold_load_seconds: float | None
    throughput_tps: float | None
    max_ctx_observed: int | None
    measured_at: str | None


class ModelCardPayload(BaseModel):
    name: str
    tag: str | None
    size_bytes: int
    config: ModelConfigPayload
    actual: ModelActualPayload
    metrics: ModelMetricsPayload | None


class DashboardSnapshot(BaseModel):
    """Mirrors UC-02 §`/api/dashboard/snapshot` payload exactly."""

    model_config = ConfigDict(extra="forbid")

    gpus: list[GpuPayload]
    columns: list[str]
    models: list[ModelCardPayload]
    last_calls: list[dict[str, Any]]
    status: str  # 'healthy' | 'degraded' | 'ollama_unreachable'
    ts: str


# --- UC-02: admin Ollama placement / settings ---------------------------


class PlaceRequest(BaseModel):
    placement: str  # 'gpuN' | 'multi_gpu' | 'on_demand' | 'available'


class PlaceApplied(BaseModel):
    keep_alive_seconds: int
    main_gpu: int | None = None
    num_gpu: int | None = None


class PlaceResponse(BaseModel):
    applied: PlaceApplied
    loaded_now: bool
    mismatch: bool = False
    main_gpu_actual: int | None = None


class ModelSettingsPatch(BaseModel):
    """All fields optional — only those present are updated."""

    keep_alive_seconds: int | None = None
    num_ctx_default: int | None = None
    single_flight: bool | None = None
    notes: str | None = None


class PullRequest(BaseModel):
    model_name: str | None = None  # accepted for spec parity; the URL path is authoritative


class PerfTestRequest(BaseModel):
    contexts: list[int] | None = None


# --- UC-04 / UC-05: chat + code -------------------------------------------


class ConversationCreateRequest(BaseModel):
    """Empty body is fine — the server picks `mode` from the route prefix
    and writes a minimal `conversations` row. Optional `model` / `system_prompt`
    can be supplied to short-circuit the default flow.
    """

    model: str | None = None
    system_prompt: str | None = None
    title: str | None = None


class ConversationCreateResponse(BaseModel):
    conversation_id: int
    mode: str


class ConversationSummary(BaseModel):
    id: int
    mode: str
    title: str | None
    model: str | None
    system_prompt: str | None
    created_at: str
    updated_at: str
    message_count: int


class MessagePayload(BaseModel):
    id: int
    role: str
    content: str
    model: str | None
    usage_in: int | None
    usage_out: int | None
    gen_tps: float | None
    latency_ms: int | None
    ts: str
    error: str | None


class ConversationDetail(BaseModel):
    id: int
    mode: str
    title: str | None
    model: str | None
    system_prompt: str | None
    created_at: str
    updated_at: str
    # Sprint 5 UX (Feature 5 — token counter): joined from model_config row
    # for the conversation's current model. None if no per-model row exists;
    # the frontend falls back to a default ctx (8192) in that case.
    num_ctx_default: int | None = None
    messages: list[MessagePayload]


class ConversationPatchRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None


class StreamRequest(BaseModel):
    content: str = Field(..., min_length=1)
    # Sprint 5 (Feature 4 — Thinking toggle): some Ollama models
    # (deepseek-r1, qwen3, ...) accept `think: true` to enable extended
    # reasoning. Default false. Models that don't recognise the option
    # ignore it silently per Ollama's docs.
    think: bool = False


class ModelPickerEntry(BaseModel):
    name: str
    tag: str | None
    size_bytes: int


# --- UC-06: admin user management ----------------------------------------


class UserSummary(BaseModel):
    """Per-row payload for the /api/admin/users table.

    Token totals are aggregated from `messages` for assistant rows on the
    user's conversations — see `services/users.get_token_totals()`.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    username: str
    role: str  # 'chat' | 'code' | 'admin' (ADR-004)
    must_change_password: bool
    created_at: datetime | None
    last_login_at: datetime | None
    deleted_at: datetime | None  # non-null = soft-deleted
    tokens_in: int
    tokens_out: int
    # Sprint 7 — soft-deactivation flag distinct from `deleted_at`.
    # 0 = login blocked (reactivatable); 1 = active.
    is_active: int = 1


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=31)
    password: str = Field(..., min_length=8)
    role: str = "chat"


class PatchRoleRequest(BaseModel):
    role: str


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)


# --- UC-06b: code working folder -----------------------------------------


class FileEntry(BaseModel):
    name: str
    path: str  # relative to user root, URL-safe (forward slashes only)
    size_bytes: int
    modified_at: datetime
    is_dir: bool


class SaveFileRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    content: str
    overwrite: bool = False
