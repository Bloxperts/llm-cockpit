<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-27 -->
# UC-09 · Test Spec — First-login forced password change

**Status:** Accepted
**User Spec:** [`../../use-cases/UC-09-first-login-password-change.md`](../../use-cases/UC-09-first-login-password-change.md)
**Functional Spec:** [`../functional/UC-09-first-login-password-change.md`](../functional/UC-09-first-login-password-change.md)

## Approach

Pure pytest + httpx. Fixture seeds an admin with `must_change_password=true`. Tests exercise the dependency, the endpoint, and the validation rules.

## Automated test cases

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1 | Login with seeded admin succeeds (200 + cookie). Subsequent `GET /api/dashboard/snapshot` returns 409 `{"detail": "must_change_password"}`. | auto |
| T-02 | AC-2 | `GET /change-password` (frontend route) renders for any role with `must_change_password=true`. | auto (request snapshot) |
| T-03 | AC-3 | `POST /api/auth/change-password` with `new_password="ollama"` returns 400 `{"detail": "cannot_reuse_default"}`. | auto |
| T-04 | AC-4 | `new_password="short"` returns 400 `{"detail": "too_short"}`. | auto |
| T-05 | AC-4 | `new_password != confirm_password` returns 400 `{"detail": "passwords_dont_match"}`. | auto |
| T-06 | AC-5, AC-6 | Successful change → 200, `must_change_password=0`, `password_changed_at` set; subsequent `GET /api/dashboard/snapshot` returns 200. | auto |
| T-07 | AC-7 | Logout, log in with new password → does not see `/change-password` again; lands on role-appropriate page. | auto |
| T-08 | AC-8 | After admin (UC-06) `POST /api/admin/users/{id}/reset-password`, that user's `must_change_password` is back to `1`; next login goes through the flow. | auto |
| T-09 | — | The 409 response carries `WWW-Authenticate: ChangePassword`. | auto (header assert) |
| T-10 | — | `POST /api/auth/change-password` without a JWT returns 401, not 409. | auto |

## Pass criteria

- All 10 cases pass.
- ≥ 90 % coverage on `cockpit/routers/auth.py` (the change-password endpoint and the dependency).
