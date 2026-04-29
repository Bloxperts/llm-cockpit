# Claude Code prompt — Sprint 7: Auth UX + Session control

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

`develop` HEAD is `0da9226` (v0.2.0). Confirm:

```bash
git fetch origin && git log origin/develop --oneline -3
```

---

## Read first

1. `CLAUDE.md`
2. `src/cockpit/routers/auth.py` — `_create_token`, `current_user`, `change-password`
3. `src/cockpit/routers/admin_users.py` — existing admin guards (last-admin check)
4. `src/cockpit/models.py` — `User` table columns
5. `frontend/src/components/ChatShell.tsx` — `sendMessage()` / `regenerate()`
6. `frontend/src/components/AppHeader.tsx`

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/sprint7-auth-ux
```

Commit prefix: `[auth]`. One PR against `develop`.

---

## What already exists — do not rebuild

- `POST /api/auth/change-password` — any authenticated user can call it; validates
  length, default-password reuse, confirm match. Already correct — no backend change
  needed for item 2, only a frontend exposure.
- `_create_token(user_id, ttl_seconds, secret)` — takes ttl as a parameter, easy to
  adapt.
- `routers/admin_users.py` — `_last_admin_guard(session)` helper (or equivalent)
  already prevents deleting the last admin. Reuse for deactivation.

---

## Database migration — `0003_auth_ux.py`

Create `src/cockpit/migrations/versions/0003_auth_ux.py`. Three new columns on `users`:

```sql
ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN session_ttl_days INTEGER NULL;       -- NULL = 7 days (legacy default)
ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
```

- `token_version` — incremented to invalidate all existing tokens for a user.
- `session_ttl_days` — user's preferred JWT lifetime. NULL means the system default
  (7 days). Store the raw integer; the options presented in the UI are 1 / 7 / 30 / 0
  (0 = unlimited = 10 years in practice).
- `is_active` — 0 = deactivated, login blocked. Distinct from `deleted_at` (permanent
  removal). A deactivated account can be reactivated by an admin.

Add the columns to the `User` ORM in `src/cockpit/models.py`:

```python
token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
session_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

Run `alembic upgrade head` to verify before committing.

---

## Item 1 — User message appears immediately in chat

**File:** `frontend/src/components/ChatShell.tsx`

In `sendMessage()` (and `regenerate()`), before the first `await` / `streamSse()`
call, optimistically push the user's draft into the displayed message list:

```typescript
// Optimistic user bubble — shown immediately, before any SSE arrives
setSelected(prev => prev ? {
  ...prev,
  messages: [
    ...prev.messages,
    {
      id: -1,           // sentinel; replaced when conversation reloads after done
      role: "user",
      content: draft,
      ts: new Date().toISOString(),
    } as MessageSummary,
  ],
} : prev);
setDraft("");
```

On the "done" event (or error), re-fetch the conversation detail as normal —
the real message row (with a real id) will replace the optimistic one.

The same pattern applies in `regenerate()`: do **not** push a second user bubble
there (the user message already exists); just clear streaming state and stream.

---

## Item 2 — Voluntary password change (UI only)

**File:** `frontend/src/components/AppHeader.tsx`

Add a **"Change password"** link in the user menu / header. When clicked, navigate
to `/change-password`. The existing page (`app/change-password/page.tsx`) already
handles the form and calls `POST /api/auth/change-password` — no changes needed
to that page or the backend.

The link should be visible to **all** logged-in users (not admin-only).

---

## Item 3 — Configurable session duration (JWT TTL)

### Backend

**`src/cockpit/routers/auth.py`**

Add a constant mapping:

```python
TTL_MAP = {1: 86_400, 7: 604_800, 30: 2_592_000, 0: 315_360_000}  # 0 = ~10 years
DEFAULT_TTL_DAYS = 7
```

Helper to resolve a user's TTL:

```python
def _user_ttl_seconds(user: User) -> int:
    days = user.session_ttl_days if user.session_ttl_days is not None else DEFAULT_TTL_DAYS
    return TTL_MAP.get(days, TTL_MAP[DEFAULT_TTL_DAYS])
```

Apply in `POST /api/auth/login`: replace the hardcoded TTL with
`_user_ttl_seconds(user)`.

Apply in the sliding-renewal block inside `current_user`: when issuing a fresh
token, use `_user_ttl_seconds(user)` for the new TTL.

**New endpoint** — `PATCH /api/auth/session-ttl`:

```python
class SessionTtlRequest(BaseModel):
    ttl_days: int  # must be in {0, 1, 7, 30}

@router.patch("/session-ttl")
async def set_session_ttl(
    body: SessionTtlRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
):
    if body.ttl_days not in TTL_MAP:
        raise HTTPException(422, "ttl_days must be one of 0, 1, 7, 30")
    user.session_ttl_days = body.ttl_days
    db.commit()
    return {"ttl_days": body.ttl_days}
```

Add `session_ttl_days: int | None` to `MeResponse` schema so the frontend
knows the current preference on load.

### Frontend

Add a **"Session duration"** dropdown to the user preferences area. Placement:
either a small profile menu in `AppHeader` or below the "Change password" link.

Options: `1 day / 7 days (default) / 30 days / Unlimited`.

On change: `PATCH /api/auth/session-ttl` → the next login will use the new TTL.
Note: the *current* session's cookie TTL does not change retroactively — only
the next issued token. Make this clear with a small hint: "Takes effect on next
login."

---

## Item 4 — Admin: revoke sessions (force re-login)

### Backend — token_version mechanism

**`src/cockpit/routers/auth.py`** — `_create_token`:

Add `tkv` (token version) to the JWT payload:

```python
def _create_token(user_id: int, token_version: int, ttl_seconds: int, secret: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return jwt.encode(
        {"sub": str(user_id), "tkv": token_version, "exp": int(exp.timestamp())},
        secret,
        algorithm=JWT_ALG,
    )
```

Update all `_create_token` call-sites to pass `user.token_version`.

**`current_user` dependency** — after loading the user from DB, verify version:

```python
if payload.get("tkv", 0) != user.token_version:
    raise HTTPException(401, detail="session_revoked")
```

**New endpoint in `src/cockpit/routers/admin_users.py`**:

```
POST /api/admin/users/{id}/revoke-sessions
```

- Requires `require_role_settled("admin")`.
- `UPDATE users SET token_version = token_version + 1 WHERE id = :id`.
- Write `AdminAudit(action='sessions_revoked', details={'target': username})`.
- Returns `{"ok": True}`.
- No guard needed (admin can revoke any user including themselves — they'll just
  be redirected to /login on next request).

### Frontend

In `frontend/src/app/admin/users/page.tsx`, add a **"Force re-login"** button
per user row (admin-only, visible to all users including other admins). On click:
`POST /api/admin/users/{id}/revoke-sessions` → show a brief success flash.

---

## Item 5 — Deactivate / reactivate accounts

Any account can be deactivated — including admin accounts — as long as at least
one **active** admin remains.

### Backend — login + current_user checks

**`POST /api/auth/login`** — after loading the user, before password check:

```python
if not user.is_active:
    raise HTTPException(403, detail="account_disabled")
```

**`current_user` dependency** — after loading user from DB:

```python
if not user.is_active:
    raise HTTPException(401, detail="account_disabled")
```

**New endpoints in `src/cockpit/routers/admin_users.py`**:

```
POST /api/admin/users/{id}/deactivate
POST /api/admin/users/{id}/reactivate
```

Both require `require_role_settled("admin")`.

**Deactivate guard** — reuse / extend the existing last-admin check:

```python
def _active_admin_count(session: Session) -> int:
    return session.execute(
        select(func.count()).where(
            User.role == "admin",
            User.is_active == 1,
            User.deleted_at.is_(None),
        )
    ).scalar_one()
```

If `target.role == "admin"` and `_active_admin_count(session) <= 1`:
→ 400 `{"detail": "last_active_admin"}`.

On deactivate:
- `user.is_active = 0`
- `user.token_version += 1` — immediately invalidates all active sessions.
- Write audit `action='user_deactivated'`.
- Return 200 `{"ok": True}`.

On reactivate:
- `user.is_active = 1`
- Write audit `action='user_reactivated'`.
- Return 200 `{"ok": True}`.

### Frontend

In `frontend/src/app/admin/users/page.tsx`:

- Replace the existing **Delete** button with a split: **Deactivate** (when active)
  / **Reactivate** (when inactive) + **Delete** (permanent, separate, with
  confirmation dialog).
- Deactivated users show a visual indicator in the table row: muted text +
  a `Inactive` badge (`bg-neutral-200 text-neutral-500`).
- Self-deactivation: hide the Deactivate button for the currently logged-in admin
  (frontend convenience — backend guards regardless).

---

## Tests

### `tests/test_sprint7_auth.py`

- Login as deactivated user → 403 `account_disabled`.
- `current_user` with deactivated user's token → 401.
- Deactivate last active admin → 400 `last_active_admin`.
- Revoke sessions → `token_version` incremented → old token rejected with 401
  `session_revoked`.
- `PATCH /session-ttl` with valid value → stored; subsequent token uses new TTL.
- `PATCH /session-ttl` with invalid value (e.g. 14) → 422.

### `tests/test_uc01_auth.py` — update existing

- `_create_token` now takes `token_version` — update all call-sites in tests.
- Add: token with stale `tkv` → 401.

---

## Coverage target

```bash
pytest --cov=cockpit.routers.auth \
       --cov=cockpit.routers.admin_users \
       --cov-report=term-missing
```

≥ 90 % on both modules.

---

## Build + release

```bash
make build

gh pr create \
  --base develop \
  --head feature/sprint7-auth-ux \
  --title "[auth] Session control, account deactivation, password UX" \
  --body "Five improvements to auth and user management:

1. Optimistic user message display — chat bubble appears immediately on send, before SSE arrives
2. Voluntary password change — Change password link in AppHeader for all users
3. Configurable session TTL — user preference (1d / 7d / 30d / unlimited), stored in users.session_ttl_days, applied on next login
4. Admin session revoke — token_version mechanism invalidates all tokens instantly; Force re-login button in /admin/users
5. Account deactivation — any account deactivatable (including admins) with last-active-admin guard; deactivation also revokes sessions; Reactivate button restores access

Migration: 0003_auth_ux.py adds token_version, session_ttl_days, is_active to users."

gh pr merge --squash \
  --subject "[auth] Session control, account deactivation, password UX" \
  --delete-branch=false

git checkout develop && git pull
git tag v0.2.1
git push origin v0.2.1
gh release create v0.2.1 dist/llm_cockpit-0.2.1-py3-none-any.whl \
  --title "v0.2.1 — Auth UX + Session control" \
  --notes "- Chat: user message appears immediately on send (optimistic UI)
- AppHeader: Change password link for all users
- Session duration preference: 1 day / 7 days / 30 days / Unlimited — stored per user, applied on next login
- Admin: Force re-login button — invalidates all sessions for a user immediately (token_version mechanism)
- Admin: Deactivate / Reactivate accounts — works for all roles including admins, with last-active-admin guard; deactivation also revokes active sessions"
```

---

## Stop and ask Chris if

- The existing last-admin check in `admin_users.py` uses a different helper name —
  read the file before writing a duplicate.
- `_create_token` is called from more places than just the login endpoint (e.g. in
  tests) — grep for all call-sites before changing the signature.
- The `MeResponse` schema change (adding `session_ttl_days`) breaks any existing
  frontend code that destructures the me response strictly.
