<!-- Status: Review | Version: 0.1 | Created: 2026-04-27 -->
# US-09 · User Spec — First-login forced password change

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-09-first-login-password-change.md`](../functional/US-09-first-login-password-change.md)
**Test Spec:** [`test/US-09-first-login-password-change.md`](../test/US-09-first-login-password-change.md)
**Sprint:** 2
**Depends on:** US-01 (login), US-08 (bootstrap creates the admin with `must_change_password=true`).
**Min role:** any.

## Story

> As a user logging in for the first time — either the seeded `admin` (US-08) or any user the admin created (US-06) — I am required to change my password before I can do anything else, so that the default password (`ollama`) and admin-set passwords don't linger and become an attack surface.

## Target state

Whenever a user with `must_change_password=true` is authenticated:

- The browser redirects them to `/change-password` regardless of which authenticated route they tried to reach.
- The backend rejects every protected route except `POST /api/auth/change-password` with HTTP 409 + body `{"detail": "must_change_password"}`.
- The change-password screen shows two fields: "New password", "Confirm new password". Submit:
  1. Validates length ≥ 8 (server-side).
  2. Validates the two fields match (client-side).
  3. Validates the new password is **not** the literal string `"ollama"` (server-side).
  4. Updates `users.pw_hash` (bcrypt), sets `users.must_change_password = 0`, sets `users.password_changed_at = now()`.
  5. Writes a row to `login_audit` with `action = "password_changed"`.
  6. Redirects to the user's normal landing page (per US-01 §AC-2).

The flow runs identically for the seeded admin and for admin-created users.

## Acceptance criteria

1. Seeded admin (US-08) logs in with `admin` / `ollama`: backend authenticates and sets the JWT cookie, but the next request (e.g. `/api/auth/me`) returns 409 `{"detail": "must_change_password"}`.
2. The `/change-password` page renders for any authenticated user with `must_change_password=true`, regardless of role.
3. Submitting `ollama` as the new password is rejected with "Cannot reuse the default password".
4. Submitting fewer than 8 characters is rejected with "Password must be at least 8 characters".
5. After a successful change, the user is redirected to their role's landing page (US-01 §AC-2).
6. After a successful change, all protected routes work; `/change-password` page is no longer required.
7. Logging out and logging back in with the new password works; the user is **not** sent through the change flow again.
8. An admin (US-06) reset of any user's password sets `must_change_password=true` again; that user goes through the flow on next login.

## Scope boundaries (out)

- Password complexity beyond minimum length and "not the literal default". No "must contain digit + symbol + uppercase" in v0.1.
- Password history / "cannot reuse last N passwords". Out.
- Email confirmation of password change. Out.
- Account lockout after N failed change-password attempts. Out (login lockout is in US-01; this flow is post-login).
- Rate limit on `/api/auth/change-password`. Out (the user is already authenticated; abuse is bounded).

## Notes

- The 409 response is deliberately distinct from 401 (unauthenticated) and 403 (under-privileged). Frontend uses 409 → "force change" redirect.
- The "not literal default password" check is a small defence-in-depth measure for users who accidentally hit Tab + Enter.
- Cross-cuts US-06: every admin-created user lands with `must_change_password=true`, so this flow is also the onboarding flow for new accounts.
