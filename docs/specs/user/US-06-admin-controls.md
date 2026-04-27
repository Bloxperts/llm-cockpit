<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# US-06 · User Spec — Admin: User management

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-06-admin-controls.md`](../functional/US-06-admin-controls.md)
**Test Spec:** [`test/US-06-admin-controls.md`](../test/US-06-admin-controls.md)
**Sprint:** 6
**Depends on:** US-01 (login), US-09 (must_change_password flow for newly-created users), ADR-004 (role ladder).
**Min role:** `admin`.

> **Note on filename:** the file is named `US-06-admin-controls.md` for v0.1 to avoid a rename mid-sprint. The story is **user management**, per ADR-003 §6 (admin scope shrunk). The Ollama-config admin surface is a separate story (US-10).

## Story

> As the `admin` I want a small page where I can add new users, delete users, change a user's role on the ladder (`chat` / `code` / `admin`), and reset a user's password, so that I can run the cockpit for my household / team without ever shelling into the host.

## Target state

`/admin/users` shows:

- **User list.** Columns: `username`, `role`, `must_change_password`, `created_at`, `last_login_at`. Sortable. Search box.
- **Add user button** → modal: `username`, `role` (default `chat`), and an option for "Set password now" (auto-generates a random password if unchecked, default checked = type a password). Created users land with `must_change_password=true` so they hit the US-09 flow on first login.
- **Per-row actions:**
  - **Set role** — dropdown changes role on submit; no password prompt; effective immediately (ADR-004 §5).
  - **Reset password** — modal with new password (visible to admin; "show generated password" if random); sets `must_change_password=true`.
  - **Delete user** — confirm modal ("This deletes the user but keeps their conversation history with `<deleted>` author"). Soft delete: row stays in `users` with `deleted_at` set so audit trails resolve.
- **Audit panel** at the bottom. Last 50 admin actions (`admin_audit` table): `ts`, `actor`, `action`, `target_username`, `details`.

Constraints:

- Admin cannot delete themselves. The "Delete" button is disabled for the row matching the logged-in admin's user id.
- Admin cannot demote themselves *if* they are the last admin. The role-change dropdown's "admin → other" disables when `count(role=admin where deleted_at IS NULL) = 1`.
- Username must be unique (case-insensitive) and match `^[a-z][a-z0-9._-]{1,30}$`.
- Password set/reset goes through bcrypt with the same cost factor as login.

## Acceptance criteria

1. Only `admin` can reach `/admin/users`. `chat` / `code` users get 403; the sidebar link is hidden for them.
2. Admin can add a new user with role `chat`, `code`, or `admin`. New row appears with `must_change_password=true`.
3. The new user logs in with the admin-set password, is forced through the US-09 password change, then can use the cockpit.
4. Admin can change any user's role via the dropdown. The change takes effect on the user's next request without re-login (per ADR-004 §5).
5. Admin can reset any user's password. The user is forced through US-09 on next login.
6. Admin can delete any non-admin user. After delete, that user can no longer log in. Their past conversations remain in the admin's "Recent calls" panel attributed to `<deleted>`.
7. Admin cannot delete themselves; the button is disabled.
8. Admin cannot demote the last admin; the dropdown option is disabled.
9. Every admin action writes a row to `admin_audit` with `ts`, `actor_user_id`, `action`, `target_user_id`, `details_json`.
10. Username validation rejects spaces, uppercase, leading digit, length &lt; 2, length &gt; 31; the form shows the regex hint inline.

## Scope boundaries (out)

- Self-service registration. Out.
- Email-based password reset. Out (admin reset is the only path in v0.1).
- Bulk import of users from CSV. Out (CLI `cockpit-admin user-add` is the headless path).
- Two-person rule for admin actions. Out (one admin's action is final in v0.1).
- Password complexity rules beyond a minimum length of 8. Out.
- Account lockout after N successful logins, weird-time alerts, etc. Out.
- All the original SPEC-006 content (pin/unpin model, num_ctx, keep_alive, power-cap, vLLM start/stop). Moved to **US-10** (admin Ollama configuration) and v0.2.

## Notes

- The `cockpit-admin` CLI exposes the same operations headless: `user-add`, `user-delete`, `user-set-role`, `user-set-password`, `user-list`. The CLI and the UI share the same backend service code; both write `admin_audit`.
- Role demotion takes effect immediately. There is no "session ends on demotion" semantics in v0.1; the next API call simply 403s and the frontend redirects.
- Delete is soft. Hard-delete (purge `users` row + cascade) is not part of v0.1.
