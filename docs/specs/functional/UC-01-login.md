<!-- Status: Accepted | Version: 0.2 | Created: 2026-04-26 | Updated: 2026-04-27 -->
# UC-01 · Functional Spec — User login

**Status:** Accepted
**Owner:** Chris
**Depends on:** none.
**User Spec:** [`../../use-cases/UC-01-login.md`](../../use-cases/UC-01-login.md)
**Test Spec:** [`../test/UC-01-login.md`](../test/UC-01-login.md)
**Bound DG:** none. Login does not cross the platform boundary outside the SQLite users table; SQLite is in-process, not an external integration.

## Goal

A family member or tester opens `http://192.168.111.200:8080` from a browser on the LAN, sees a login form, types username + password, and lands on the dashboard.

## Functional requirements

- F1. The `/login` page shows two fields (username, password) and one button.
- F2. On submit, the backend validates the password against a bcrypt hash for that username.
- F3. On success, the backend issues a JWT and sets it as an HTTP-only cookie. Frontend redirects to `/dashboard`.
- F4. On failure, the form shows a generic "Invalid credentials" message; no information leak about which field was wrong.
- F5. JWT lifetime: 7 days. Sliding renewal on any authenticated API call (response sets a refreshed cookie if the token is more than 1 day from expiry).
- F6. Logout endpoint clears the cookie.
- F7. Visiting any authed route without a valid JWT redirects to `/login`.

## Non-functional requirements

- Rate limiting: 5 failed logins per username per 5 min → temporary lockout (return 429 with hint at 60 s back-off). In-memory counter is sufficient for a 5-user system.
- Logging: every login attempt (success/fail) lands in a `login_audit` table with timestamp, username, and source IP.
- No password reset flow in v0.1. Admin (Chris) edits the password file via CLI.

## Data model

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','user')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE login_audit (
  id INTEGER PRIMARY KEY,
  username TEXT,
  success INTEGER NOT NULL,
  source_ip TEXT,
  ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## API

```
POST /api/auth/login        body { username, password }
                            → 200 { user: {id, username, role}, ttl_seconds }
                            → 401 on bad creds
                            → 429 on lockout

GET  /api/auth/me           → 200 { id, username, role }   (requires JWT)
                            → 401 if no/invalid JWT

POST /api/auth/logout       → 200 (clears cookie)
```

## CLI for admin

```
$ cockpit-admin user-add --username chris --role admin
Password (hidden): ****
$ cockpit-admin user-set-password --username chris
$ cockpit-admin user-list
```

The CLI writes directly to the SQLite DB.

## Acceptance criteria

- ✅ Wrong password → "Invalid credentials" within 1 s, no clue which field.
- ✅ Right password → redirect to dashboard within 1 s.
- ✅ JWT cookie present in browser DevTools after login, HttpOnly + SameSite=Strict.
- ✅ Manually deleting the cookie → next API call returns 401, page redirects to /login.
- ✅ Six bad attempts in a minute → 429 on the seventh.
- ✅ Login audit row exists for every attempt.

## Test plan

1. Provision 3 users via CLI (chris/admin, mila/user, tester/user).
2. Each user logs in successfully.
3. Each user fails once with wrong password.
4. JWT validation tested by manually editing the cookie value.
5. Lockout tested with a script that hammers 6 attempts in 30 s.

## Out of scope

- 2FA / TOTP.
- OAuth / SAML / OpenID Connect.
- Password recovery flow.
- Self-service registration.
