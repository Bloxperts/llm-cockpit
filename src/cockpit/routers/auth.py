"""Auth router — login / me / logout + JWT issuance + dependencies.

Per UC-01 functional spec + ADR-004:

- JWT (HS256) carries only `sub` = str(user.id); role is **not** baked in.
  Roles are resolved from `users` on every request so an admin role flip
  takes effect immediately (ADR-004 §5).
- Cookie: `cockpit_jwt`, HttpOnly, SameSite=Strict, Path=/, no Secure.
  LAN-only HTTP per GOALS.md / ADR-003. Reverse-proxy + TLS is the
  operator's job.
- Sliding renewal: any authenticated request whose token is < 1 day from
  expiry gets a refreshed cookie on the response (UC-01 F5).
- Rate limit: 5 failed attempts per username in a 5-minute window → 429
  with `retry_after_seconds=60`. Lives on `app.state.rate_limiter`, so each
  `create_app()` instance has a clean slate (good for tests, harmless for
  the single-process production run).
- `login_audit` row written for every login attempt (success + fail);
  also for logout (action='logout') and — in UC-09 — password changes
  (action='password_changed').
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from cockpit.config import Settings
from cockpit.deps import get_session, get_settings
from cockpit.models import LoginAudit, User
from cockpit.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    SessionTtlRequest,
)
from cockpit.services.users import (
    DEFAULT_ADMIN_PASSWORD,
    get_user_by_id,
    get_user_by_username,
    update_last_login,
    update_password,
    verify_password,
)

router = APIRouter()

COOKIE_NAME = "cockpit_jwt"
JWT_ALG = "HS256"

# UC-01 NFR: 5 failures per username per 5 minutes → 429 with 60 s back-off.
DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_S = 300
DEFAULT_LOCKOUT_S = 60

# Sliding renewal threshold per UC-01 F5: refresh if < 1 day to expiry.
SLIDING_RENEWAL_THRESHOLD = timedelta(days=1)


# --- Rate limiter ----------------------------------------------------------


class RateLimiter:
    """In-memory per-username failure counter.

    Lives on `app.state.rate_limiter` so each `create_app()` instance has a
    fresh limiter — clean for tests, harmless for the single-process
    production run. Five users at five-failures-per-five-minutes is bounded;
    no eviction needed for v0.1.
    """

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_s: int = DEFAULT_WINDOW_S,
        lockout_s: int = DEFAULT_LOCKOUT_S,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, username: str, *, now: float | None = None) -> tuple[bool, int]:
        now = now if now is not None else time.monotonic()
        until = self._locked_until.get(username, 0.0)
        if now < until:
            return True, max(1, int(until - now))
        return False, 0

    def record_failure(self, username: str, *, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        recent = [t for t in self._failures.get(username, []) if now - t <= self.window_s]
        recent.append(now)
        self._failures[username] = recent
        if len(recent) >= self.max_failures:
            self._locked_until[username] = now + self.lockout_s

    def record_success(self, username: str) -> None:
        self._failures.pop(username, None)
        self._locked_until.pop(username, None)


def get_rate_limiter(request: Request) -> RateLimiter:
    """Dependency: pull the limiter off `app.state`. Created lazily so a
    TestClient that didn't go through `create_app` still works.
    """
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = RateLimiter()
        request.app.state.rate_limiter = limiter
    return limiter


# --- JWT + cookie helpers --------------------------------------------------


def _create_token(
    user_id: int,
    ttl_seconds: int,
    secret: str,
    *,
    token_version: int = 0,
) -> str:
    """Mint a JWT carrying `sub`, `tkv`, and `exp`.

    Sprint 7 added the `tkv` claim — it must equal the user's current
    `token_version` for the token to validate. Bumping the column
    invalidates every outstanding token in O(1) (no token blacklist
    needed). `token_version` is a keyword-only arg with a default of 0
    so existing test fixtures that don't supply it stay working.
    """
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return jwt.encode(
        {"sub": str(user_id), "tkv": int(token_version), "exp": int(exp.timestamp())},
        secret,
        algorithm=JWT_ALG,
    )


# Sprint 7 — per-user JWT lifetime preference. Values are days; 0 means
# "essentially unlimited" (10 years). The PATCH endpoint validates that
# `ttl_days` is a key in this dict.
TTL_MAP: dict[int, int] = {
    1: 86_400,
    7: 7 * 86_400,
    30: 30 * 86_400,
    0: 10 * 365 * 86_400,
}
DEFAULT_TTL_DAYS = 7


def _user_ttl_seconds(user: User) -> int:
    """Resolve the user's preferred token lifetime in seconds.

    NULL preference → fall back to the system default (7 days). Unknown
    values (shouldn't happen — endpoint validates) also fall back so a
    misconfigured row never breaks login.
    """
    days = (
        user.session_ttl_days
        if user.session_ttl_days is not None
        else DEFAULT_TTL_DAYS
    )
    return TTL_MAP.get(days, TTL_MAP[DEFAULT_TTL_DAYS])


def _decode_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=[JWT_ALG])


def _set_cookie(response: Response, token: str, ttl_seconds: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,  # LAN-only HTTP per ADR-003 / GOALS.md
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def _audit(
    session: Session,
    *,
    username: str | None,
    success: bool,
    source_ip: str | None,
    action: str = "login",
) -> None:
    session.add(
        LoginAudit(
            username=username,
            success=1 if success else 0,
            source_ip=source_ip,
            action=action,
        )
    )


# --- Dependencies ----------------------------------------------------------


def current_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Decode the cookie, look the user up in the DB, refresh the cookie
    when it's near expiry. Raises 401 on any failure (no info-leak about
    which step failed).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, detail="not_authenticated")
    try:
        payload = _decode_token(token, settings.jwt_secret)
    except JWTError as exc:
        raise HTTPException(401, detail="not_authenticated") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(401, detail="not_authenticated")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, detail="not_authenticated") from exc

    user = get_user_by_id(db, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(401, detail="not_authenticated")

    # Sprint 7: deactivated accounts can't act on existing sessions.
    # Distinct from `not_authenticated` so the frontend can show a useful
    # message instead of a generic "log in again".
    if not user.is_active:
        raise HTTPException(401, detail="account_disabled")

    # Sprint 7: token-version revocation (admin "Force re-login" + auto-
    # invalidation on deactivate). A bumped `users.token_version` makes
    # every JWT minted with the prior version fail validation — no token
    # blacklist needed.
    if int(payload.get("tkv", 0)) != int(user.token_version):
        raise HTTPException(401, detail="session_revoked")

    # Sliding renewal — UC-01 F5. Sprint 7 honours the user's preferred
    # TTL when re-issuing.
    exp_ts = payload.get("exp")
    if exp_ts is not None:
        exp_dt = datetime.fromtimestamp(int(exp_ts), tz=timezone.utc)
        if exp_dt - datetime.now(timezone.utc) < SLIDING_RENEWAL_THRESHOLD:
            ttl = _user_ttl_seconds(user)
            fresh = _create_token(
                user.id, ttl, settings.jwt_secret, token_version=user.token_version
            )
            _set_cookie(response, fresh, ttl)

    return user


def require_role(min_role: str):
    """Returns a dependency that raises 403 unless the user's role is
    `>= min_role` on the ladder `chat < code < admin`.
    """
    rank = {"chat": 0, "code": 1, "admin": 2}
    if min_role not in rank:
        raise ValueError(f"unknown role: {min_role}")

    def dep(user: User = Depends(current_user)) -> User:
        if rank.get(user.role, -1) < rank[min_role]:
            raise HTTPException(403, detail="insufficient_role")
        return user

    return dep


def current_user_must_be_settled(
    user: User = Depends(current_user),
) -> User:
    """UC-09 dependency: refuse every protected route except `/me` and
    `/change-password` until the user has set a real password.

    Returns 409 with `{"detail": "must_change_password"}` and
    `WWW-Authenticate: ChangePassword` so external API clients can detect
    the state.
    """
    if user.must_change_password:
        raise HTTPException(
            409,
            detail="must_change_password",
            headers={"WWW-Authenticate": "ChangePassword"},
        )
    return user


def require_role_settled(min_role: str):
    """Compose `require_role(min_role)` + `current_user_must_be_settled`.

    Routers that gate on both role + the UC-09 forced-change flow (UC-04
    chat, UC-05 code, etc.) use this helper to avoid hand-rolling the
    settled check on every endpoint.
    """
    role_dep = require_role(min_role)

    def dep(user: User = Depends(role_dep)) -> User:
        if user.must_change_password:
            raise HTTPException(
                409,
                detail="must_change_password",
                headers={"WWW-Authenticate": "ChangePassword"},
            )
        return user

    return dep


# --- Endpoints -------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> LoginResponse:
    locked, retry_seconds = limiter.is_locked(body.username)
    if locked:
        raise HTTPException(
            429,
            detail={"detail": "too_many_attempts", "retry_after_seconds": retry_seconds},
            headers={"Retry-After": str(retry_seconds)},
        )

    user = get_user_by_username(db, body.username)
    source_ip = _client_ip(request)

    valid = (
        user is not None
        and user.deleted_at is None
        and verify_password(body.password, user.pw_hash)
    )
    if not valid:
        _audit(db, username=body.username, success=False, source_ip=source_ip, action="login")
        db.commit()
        limiter.record_failure(body.username)
        raise HTTPException(401, detail="Invalid credentials")

    assert user is not None  # narrows for the type checker

    # Sprint 7: deactivated accounts are blocked at login. We log the
    # attempt as a *failed* login (with a distinct action) so the audit
    # trail captures attempts to use a disabled account.
    if not user.is_active:
        _audit(
            db,
            username=user.username,
            success=False,
            source_ip=source_ip,
            action="login_blocked_inactive",
        )
        db.commit()
        raise HTTPException(403, detail="account_disabled")

    _audit(db, username=user.username, success=True, source_ip=source_ip, action="login")
    update_last_login(db, user)
    db.commit()
    limiter.record_success(body.username)

    ttl = _user_ttl_seconds(user)
    token = _create_token(
        user.id, ttl, settings.jwt_secret, token_version=user.token_version
    )
    _set_cookie(response, token, ttl)

    return LoginResponse(
        user=MeResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            must_change_password=bool(user.must_change_password),
            session_ttl_days=user.session_ttl_days,
        ),
        ttl_seconds=ttl,
    )


@router.patch("/session-ttl")
def set_session_ttl(
    body: SessionTtlRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
) -> dict:
    """Sprint 7 — let a user pick their preferred JWT lifetime.

    Validation: `ttl_days` must be one of {0, 1, 7, 30}. `0` means
    "essentially unlimited" (10 years). The change applies to the next
    token minted (login or sliding renewal); the current cookie keeps
    its existing TTL until then.
    """
    if body.ttl_days not in TTL_MAP:
        raise HTTPException(
            422,
            detail={
                "detail": "invalid_ttl_days",
                "allowed": sorted(TTL_MAP.keys()),
            },
        )
    user.session_ttl_days = body.ttl_days
    db.flush()
    db.commit()
    return {"ttl_days": body.ttl_days}


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user)) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=bool(user.must_change_password),
        session_ttl_days=user.session_ttl_days,
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(current_user_must_be_settled),
    db: Session = Depends(get_session),
) -> dict:
    """Clear the cookie + write `login_audit(action='logout')`.

    Per UC-09: gated by `current_user_must_be_settled`. A user who hasn't
    finished their forced password change can't yet sign out — they'd
    typically clear the cookie client-side anyway. The two protected
    routes that **don't** require settled status are `/me` and
    `/change-password`.
    """
    _audit(db, username=user.username, success=True, source_ip=_client_ip(request), action="logout")
    db.commit()
    _clear_cookie(response)
    return {}


# --- UC-09: change-password ----------------------------------------------


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(current_user),  # NOT settled — this IS the settle gate
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """UC-09. Server-side validation:

    - new_password == confirm_password (else 400 passwords_dont_match).
    - len(new_password) >= 8 (else 400 too_short).
    - new_password != literal "ollama" (else 400 cannot_reuse_default).

    On success: bcrypt-hash, flip must_change_password=0, stamp
    password_changed_at, write login_audit(action='password_changed').
    Issue a fresh JWT cookie too so the now-settled user gets a normal-aged
    session without re-entering their old password.
    """
    if body.new_password != body.confirm_password:
        raise HTTPException(400, detail="passwords_dont_match")
    # Literal-default check fires *before* the length check so submitting
    # "ollama" produces a precise error message rather than a confusing
    # "too short" (which is also true — len('ollama') == 6 — but unhelpful).
    if body.new_password == DEFAULT_ADMIN_PASSWORD:
        raise HTTPException(400, detail="cannot_reuse_default")
    if len(body.new_password) < 8:
        raise HTTPException(400, detail="too_short", headers={"X-Password-Min": "8"})

    update_password(db, user, body.new_password, bcrypt_cost=settings.bcrypt_cost)
    _audit(
        db,
        username=user.username,
        success=True,
        source_ip=_client_ip(request),
        action="password_changed",
    )
    db.commit()

    # Re-issue cookie so the user gets a fresh full-TTL session post-change.
    # Honour the user's preferred TTL (Sprint 7) and current token_version.
    ttl = _user_ttl_seconds(user)
    token = _create_token(
        user.id, ttl, settings.jwt_secret, token_version=user.token_version
    )
    _set_cookie(response, token, ttl)
    return {}
