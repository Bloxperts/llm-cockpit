"""Auth router skeleton.

This is the UC-08 Slice B placeholder: just enough surface for `main.py` to
mount and for the smoke test to confirm `/api/auth/me` returns 401 before any
session exists. UC-01 (next commit on `feature/sprint2-mvp`) replaces every
endpoint here with the real login / me / logout implementation, plus
`current_user`, `require_role`, JWT issuance, the in-memory rate limiter,
and `login_audit` writes.

Do not depend on the bodies in this file from anywhere else — they only
exist so the app can boot.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/me")
async def me_placeholder() -> dict:
    """UC-08 Slice B placeholder. Always 401 until UC-01 lands real auth."""
    raise HTTPException(401, detail="not_authenticated")
