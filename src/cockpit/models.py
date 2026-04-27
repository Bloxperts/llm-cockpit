"""SQLAlchemy ORM for the v0.1 schema.

This module defines the six tables in scope for Slice A of UC-08:
`users`, `login_audit`, `model_tags`, `settings`, `model_config`, `model_perf`.

Tables `conversations`, `messages`, `metrics_snapshot`, and `admin_audit` land
in their respective UC sprints (UC-04 / UC-05 chat, UC-02 dashboard, UC-06
admin user management).

ADR-004 (role ladder), ADR-005 (per-model lifecycle), and COMPONENTS.md §4 are
the governing references.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
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
