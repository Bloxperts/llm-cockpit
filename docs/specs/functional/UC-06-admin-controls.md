<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# UC-06 · Functional Spec — Admin: User management

**Status:** Accepted
**Depends on:** UC-01 (login), UC-09 (must_change_password flow for admin-created users), ADR-004 (role ladder).
**Min role:** `admin`.
**User Spec:** [`../../use-cases/UC-06-admin-controls.md`](../../use-cases/UC-06-admin-controls.md)
**Test Spec:** [`../test/UC-06-admin-controls.md`](../test/UC-06-admin-controls.md)
**Bound DG:** none. User management writes to local SQLite only; no external boundary.

> **History note:** filename retained as `UC-06-admin-controls.md` to avoid a rename mid-sprint. The original SPEC-006 covered model lifecycle (pin/unpin/`num_ctx`/`keep_alive`/power-cap/vLLM); per ADR-003 §6 those are deferred to v0.2 ("Model Lifecycle"). v0.1 admin = user management only.

## Goal

The `admin` (and only the admin) can manage cockpit accounts: list, add, delete, set role, reset password. Every state change is audited. Admin-created accounts go through the UC-09 forced password change on first login.

## Data model

```sql
-- existing from UC-01:
CREATE TABLE users (
  id              INTEGER PRIMARY KEY,
  username        TEXT UNIQUE NOT NULL CHECK (username GLOB '[a-z][a-z0-9._-]*'),
  pw_hash         TEXT NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('chat', 'code', 'admin')),
  must_change_password INTEGER NOT NULL DEFAULT 0,
  password_changed_at TEXT NULL,
  created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at   TEXT NULL,
  deleted_at      TEXT NULL                                            -- soft delete
);

-- new (this story):
CREATE TABLE admin_audit (
  id              INTEGER PRIMARY KEY,
  ts              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actor_user_id   INTEGER NOT NULL REFERENCES users(id),
  action          TEXT NOT NULL,        -- 'user_add', 'user_delete', 'user_set_role', 'user_set_password', 'model_tag_set', 'setting_update', ...
  target_user_id  INTEGER NULL REFERENCES users(id),
  target_model    TEXT NULL,
  details_json    TEXT NULL
);
CREATE INDEX idx_admin_audit_ts ON admin_audit(ts);
CREATE INDEX idx_admin_audit_actor ON admin_audit(actor_user_id);
```

Soft-delete pattern: `deleted_at` column. All read queries filter `WHERE deleted_at IS NULL`. The `messages` table joins `LEFT JOIN users` so deleted users still resolve their past calls as `<deleted>`.

## API

```
GET    /api/admin/users                    → list users (filters: role?, q? for username search) + last_login_at
POST   /api/admin/users                    → 201 { id, username, role, must_change_password }
                                              body { username, role, password? } 
                                              if password absent → server generates random 16-char and returns it once
PATCH  /api/admin/users/{id}/role          → 200 { role }                  body { role }
POST   /api/admin/users/{id}/reset-password→ 200 { temp_password }         body { password? }  ; sets must_change_password=1
DELETE /api/admin/users/{id}               → 204                              soft delete (sets deleted_at)
GET    /api/admin/audit                    → list admin_audit rows, paginated, filterable
```

All admin routes are gated by `Depends(require_role("admin"))` per ADR-004 §4.

Defensive constraints, enforced server-side:

- Username regex: `^[a-z][a-z0-9._-]{1,30}$`. Returned 400 with `{"detail": "invalid_username", "hint": "..."}`.
- Role values: only `chat`, `code`, `admin`. Other values → 400.
- Password length: ≥ 8 characters. < 8 → 400.
- Self-delete: `target_user_id == actor_user_id` → 409 `{"detail": "cannot_self_delete"}`.
- Last-admin demotion: count of `role='admin' AND deleted_at IS NULL` would drop to 0 → 409 `{"detail": "cannot_demote_last_admin"}`.

## CLI

`cockpit-admin` exposes the same operations (per ADR-002 v1.1):

```
cockpit-admin user-add --username NAME --role chat|code|admin [--password PASS]
cockpit-admin user-delete --username NAME
cockpit-admin user-set-role --username NAME --role chat|code|admin
cockpit-admin user-set-password --username NAME [--password PASS]
cockpit-admin user-list [--role ROLE] [--include-deleted]
```

CLI and HTTP routes share the same `app/services/users.py` layer — both write `admin_audit` with `action`, `actor` (CLI uses `actor_user_id = users(username='admin')` if running as the local OS user; otherwise the OS user is recorded in `details_json`).

## Frontend layout

- `/admin/users` — table with columns: `username`, `role`, `must_change_password` (chip), `created_at`, `last_login_at`, actions.
- "Add user" → modal: `username`, `role`, "set password now" toggle (default on).
- Per-row "Edit role" inline dropdown.
- Per-row "Reset password" → modal showing the new (typed or generated) password once.
- Per-row "Delete" → confirm modal. Disabled for self.
- Audit panel below the table — last 50 rows, filterable.

## Acceptance criteria

- See User Spec §Acceptance criteria. The Test Spec (Sprint 6) translates each into automated cases.

## DG / DP compliance

- DP-002 (debuggability) — every state change writes `admin_audit`.
- DP-013 (memory write boundaries) — `users`, `admin_audit`, and `login_audit` are the only tables this router writes to; chat / dashboard / code routers do not write here.
- DP-031 (progressive autonomy) — admin role gates the whole router.
- ADR-004 §5 — role flips take effect on the user's next request without re-login.
