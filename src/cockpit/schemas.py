"""Pydantic v2 request/response models.

Centralised here so routers and tests share one shape per payload. Keep
schemas thin — they're wire-format types, not domain types. The ORM types
live in `cockpit.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
