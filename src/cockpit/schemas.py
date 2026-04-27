"""Pydantic v2 request/response models.

Centralised here so routers and tests share one shape per payload. Keep
schemas thin — they're wire-format types, not domain types. The ORM types
live in `cockpit.models`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)


class MeResponse(BaseModel):
    """Identity payload used by `GET /api/auth/me` and embedded in
    `LoginResponse.user`. The `must_change_password` field tells the frontend
    whether to redirect the user through the UC-09 forced-change flow.
    """

    id: int
    username: str
    role: str  # 'chat' | 'code' | 'admin' (ADR-004; enforced at the DB CHECK)
    must_change_password: bool


class LoginResponse(BaseModel):
    user: MeResponse
    ttl_seconds: int


class ChangePasswordRequest(BaseModel):
    """UC-09. Server validates length ≥ 8, equality, and != literal 'ollama'."""

    new_password: str
    confirm_password: str


class LogoutResponse(BaseModel):
    pass


# --- UC-02: dashboard snapshot --------------------------------------------


class GpuPayload(BaseModel):
    index: int
    vram_used_mb: int
    vram_total_mb: int
    temp_c: float | None
    power_w: float | None


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
    messages: list[MessagePayload]


class ConversationPatchRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None


class StreamRequest(BaseModel):
    content: str = Field(..., min_length=1)


class ModelPickerEntry(BaseModel):
    name: str
    tag: str | None
    size_bytes: int
