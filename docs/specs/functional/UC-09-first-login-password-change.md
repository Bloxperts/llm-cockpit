<!-- Status: Done (technical) | Version: 0.1 | Created: 2026-04-27 | Updated: 2026-04-28 -->
# UC-09 · Functional Spec — First-login forced password change

**Status:** Done (technical)
<!-- VAULT-SYNC: implementation landed in feature/sprint2-mvp on top of UC-01. Status flipped Accepted → In Progress → Done (technical). One small implementation note vs the spec's prose: the literal "ollama" check fires *before* the length check so submitting "ollama" produces `cannot_reuse_default` rather than `too_short` (len('ollama')=6, would have failed both). The Test Spec asserts cannot_reuse_default, so this ordering matches the test. Mirror in vault and re-sync /docs at sprint review. User Acceptance pending Chris's sprint-review sign-off. -->

**Depends on:** UC-01 (login), UC-08 (bootstrap creates the admin with `must_change_password=true`).
**User Spec:** [`../../use-cases/UC-09-first-login-password-change.md`](../../use-cases/UC-09-first-login-password-change.md)
**Test Spec:** [`../test/UC-09-first-login-password-change.md`](../test/UC-09-first-login-password-change.md)
**Bound DG:** none. SQLite-only writes; no external boundary.

## Goal

Force any user with `users.must_change_password = 1` through a password-change screen before any other action is allowed.

## Backend

### Middleware / dependency

A FastAPI dependency `current_user_must_be_settled` runs after `current_user`. It checks `users.must_change_password`:

```python
def current_user_must_be_settled(user: User = Depends(current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(409, "must_change_password")
    return user
```

Every protected route except `POST /api/auth/change-password` and `GET /api/auth/me` uses `current_user_must_be_settled`. The two exceptions use `current_user`. (`/api/auth/me` returns the user's identity *and* the `must_change_password` flag so the frontend knows to redirect.)

### Endpoint

```
POST /api/auth/change-password         body { new_password, confirm_password }
                                       → 200 {}                              on success
                                       → 400 { detail: "passwords_dont_match" }
                                       → 400 { detail: "too_short", min: 8 }
                                       → 400 { detail: "cannot_reuse_default" }
                                       → 401                                   if not authenticated
```

On success:
- Hash the new password with bcrypt at the configured cost.
- `UPDATE users SET pw_hash=?, must_change_password=0, password_changed_at=now() WHERE id=?`.
- Insert `login_audit (action='password_changed', success=1, source_ip=…)`.
- Return 200; the frontend redirects to the role's landing page.

### Validation rules (server-side)

- Length ≥ 8 characters.
- `new_password == confirm_password`.
- `new_password != "ollama"` (literal compare).

## Frontend

- `/change-password` route. Two password fields ("New password", "Confirm new password"), Submit button.
- The frontend's API wrapper intercepts any 409 with `{"detail": "must_change_password"}` and redirects to `/change-password`. This handles the case where a user with a stale tab tries to act after an admin reset.
- After successful change, the wrapper navigates to `/api/auth/me`, reads the role, and pushes the user to:
  - `admin` → `/dashboard`
  - `code` → `/chat` (default landing) or last-used page
  - `chat` → `/chat`

## Acceptance criteria

- See User Spec §Acceptance criteria.
- The 409 response carries `WWW-Authenticate: ChangePassword` so external API clients can detect the state.
- The endpoint is rate-limit-exempt at the IP level (the user is already authenticated; no abuse vector there).

## Cross-cuts

- UC-01: must use `current_user_must_be_settled` on every protected route except the two exceptions.
- UC-06: every admin-created user lands with `must_change_password=true`. Reset password flips it back to `1`.
- UC-08: bootstrap seeds the admin with `must_change_password=true`.
