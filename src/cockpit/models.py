"""SQLAlchemy ORM for the v0.1 schema.

UC-08 Slice A defined six tables: `users`, `login_audit`, `model_tags`,
`settings`, `model_config`, `model_perf`. UC-02 (Sprint 3) added
`metrics_snapshot` (GPU sampler) and `admin_audit` (state-changing admin
actions). UC-04 / UC-05 (Sprint 4) add `conversations` and `messages`.

ADR-004 (role ladder), ADR-005 (per-model lifecycle), and COMPONENTS.md §4 are
the governing references.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    must_change_password: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Sprint 7 — auth UX. See migration 0003_auth_ux.py for column docs.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    session_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    __table_args__ = (
        CheckConstraint("role IN ('chat', 'code', 'admin')", name="ck_users_role"),
    )


class LoginAudit(Base):
    __tablename__ = "login_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False, default="login")


class ModelTag(Base):
    __tablename__ = "model_tags"

    model: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint("tag IN ('chat', 'code', 'both')", name="ck_model_tags_tag"),
        CheckConstraint("source IN ('auto', 'override')", name="ck_model_tags_source"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class ModelConfig(Base):
    __tablename__ = "model_config"

    model: Mapped[str] = mapped_column(String, primary_key=True)
    placement: Mapped[str] = mapped_column(String, nullable=False, default="on_demand")
    keep_alive_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_ctx_default: Mapped[int | None] = mapped_column(Integer, nullable=True)
    single_flight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "placement IN ('on_demand', 'gpu0', 'gpu1', 'gpu2', 'gpu3', 'multi_gpu', 'available')",
            name="ck_model_config_placement",
        ),
    )


class ModelPerf(Base):
    __tablename__ = "model_perf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    cold_load_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_token_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    throughput_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_ctx_observed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu_layout_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_model_perf_model_ts", "model", "measured_at"),
    )


# --- UC-02: dashboard / admin lifecycle audit -----------------------------


class MetricsSnapshot(Base):
    """One row per GPU per sample (5 s cadence, GpuSampler in services/metrics.py)."""

    __tablename__ = "metrics_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("idx_metrics_snapshot_ts", "ts"),
        Index("idx_metrics_snapshot_gpu_ts", "gpu_index", "ts"),
    )


class MetricsSnapshotMinute(Base):
    """UC-03 — 1-minute down-sample of `metrics_snapshot`.

    One row per (closed minute, gpu_index). Populated by
    `services.aggregator.MinuteAggregator`. The 24 h history chart reads
    from this table; raw `metrics_snapshot` is pruned to 7 d so this is
    effectively the long-tail store for the minute granularity.
    """

    __tablename__ = "metrics_snapshot_minute"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb_avg: Mapped[float] = mapped_column(Float, nullable=False)
    temp_c_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_msm_gpu_ts", "gpu_index", "bucket_ts"),
        UniqueConstraint("bucket_ts", "gpu_index", name="uq_msm_bucket_gpu"),
    )


class MetricsSnapshotHour(Base):
    """UC-03 — 1-hour down-sample of `metrics_snapshot_minute`.

    One row per (closed hour, gpu_index). Populated by
    `services.aggregator.HourAggregator`. The 7 d history chart reads
    from this table.
    """

    __tablename__ = "metrics_snapshot_hour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gpu_index: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_used_mb_avg: Mapped[float] = mapped_column(Float, nullable=False)
    temp_c_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_w_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_msh_gpu_ts", "gpu_index", "bucket_ts"),
        UniqueConstraint("bucket_ts", "gpu_index", name="uq_msh_bucket_gpu"),
    )


class Conversation(Base):
    """UC-04 / UC-05 — one row per chat or code conversation.

    `mode` discriminates: 'chat' or 'code'. Defaults to 'chat'.
    `system_prompt` is per-conversation (UC-04 §Persistence + UC-05 default).
    `model` is the originally-picked model; mid-conversation model switches
    are recorded on `messages.model` rather than mutating this column.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String, nullable=False, default="chat")
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint("mode IN ('chat', 'code')", name="ck_conversations_mode"),
        Index("idx_conversations_user_mode", "user_id", "mode"),
        Index("idx_conversations_user_updated", "user_id", "updated_at"),
    )


class Message(Base):
    """UC-04 / UC-05 — one row per turn in a conversation.

    `role` is 'user' / 'assistant' / 'system'. `model` is the model that
    *produced* this message (assistant rows) or that was active when the
    user typed (user rows — handy for analytics).
    `usage_in` / `usage_out` / `gen_tps` / `latency_ms` are extracted from
    the final NDJSON chunk per UC-04 AC-9; `error` is `'stream_aborted'`
    when the stream was cut mid-emission.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    usage_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gen_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_messages_role"
        ),
        Index("idx_messages_conversation_ts", "conversation_id", "ts"),
        # UC-03 — needed by the call-rate / latency / tokens history queries
        # which filter on `ts` only. The composite (conversation_id, ts)
        # above can't serve a `ts`-only WHERE per the leftmost-prefix rule.
        Index("idx_messages_ts", "ts"),
    )


class AdminAudit(Base):
    """One row per state-changing admin action (place / pull / delete /
    settings_patch / perf_test). Per COMPONENTS.md §4 + DP-013.
    """

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_model: Mapped[str | None] = mapped_column(String, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_admin_audit_ts", "ts"),
        Index("idx_admin_audit_action", "action"),
    )
