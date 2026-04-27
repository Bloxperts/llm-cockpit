<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 -->
# US-01 · Test Spec — Login

**Status:** Review
**Owner:** Chris
**User Spec:** [`user/US-01-login.md`](../user/US-01-login.md)
**Functional Spec:** [`functional/US-01-login.md`](../functional/US-01-login.md)

## Approach

A mix of automated backend tests (pytest + httpx against the FastAPI app) and a small set of manual UI smoke checks. The backend tests are the primary gate; the manual checks validate the cookie / redirect behaviour that's awkward to assert in pytest.

The functional tests run against an in-process `TestClient` with a temporary SQLite DB seeded by a fixture. The UI smoke runs against `next dev` + `uvicorn --reload` on the developer's Mac.

## Fixtures

`fixture: three_users`

| username | password (plaintext, fixture only) | role  |
|----------|-----------------------------------|-------|
| chris    | `t3st_chris`                      | admin |
| mila     | `t3st_mila`                       | user  |
| tester1  | `t3st_tester`                     | user  |

Bcrypt hashes for these passwords are generated at fixture setup; the database is populated via the `cockpit-admin` CLI invocation, not via direct INSERT, so the CLI itself is exercised once.

## Automated test cases (pytest + httpx)

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1 | `GET /login` returns 200 with the login form HTML for an unauthenticated client. | auto |
| T-02 | AC-2 | Each fixture user can `POST /api/auth/login` with the correct password and gets a 200 + JWT cookie. | auto |
| T-03 | AC-3 | Wrong password for a known user returns 401 with body `{"detail":"Invalid credentials"}`. | auto |
| T-04 | AC-3 | Unknown username with any password returns the **same** 401 + body. (No information leak.) | auto |
| T-05 | AC-4 | Six failed `POST /api/auth/login` against `mila` within 60 s: the seventh returns 429 with `Retry-After: 60`. | auto |
| T-06 | AC-4 | After 60 s of cooldown, `mila` can log in successfully. | auto |
| T-07 | AC-5 | After successful login, the response cookie has `HttpOnly`, `Secure=false` (LAN), `SameSite=Strict`. | auto |
| T-08 | AC-6 | Token issued at `t0` with 7 d lifetime: a request at `t0 + 6 d 1 h` triggers a `Set-Cookie` refresh in the response. | auto |
| T-09 | AC-7 | `GET /api/dashboard/snapshot` without a JWT returns 401 and `Location` points to `/login`. | auto |
| T-10 | AC-8 | After `POST /api/auth/logout`, the cookie is cleared and a subsequent `GET /api/auth/me` returns 401. | auto |
| T-11 | AC-9 | After T-02 + T-03, `SELECT count(*) FROM login_audit` returns 6 (3 success + 3 fail), each row has `ts`, `username`, `success`, `source_ip`. | auto |
| T-12 | AC-10 | `GET /api/auth/me` for `chris` returns `{role: "admin"}`; for `mila` returns `{role: "user"}`. | auto |

## Manual smoke (UI)

| ID | Description | Expected |
|----|-------------|----------|
| M-01 | Open `http://localhost:3000/` in Firefox; type `chris` + `t3st_chris`; submit. | Browser lands on `/dashboard` within 1 s. Cookie `cockpit_jwt` is `HttpOnly`. |
| M-02 | While on `/dashboard`, delete the `cockpit_jwt` cookie via DevTools and click any nav item. | Browser redirected to `/login`. |
| M-03 | Open `/admin` directly while logged in as `mila`. | Browser redirected to `/dashboard` (no admin route exposed). |
| M-04 | Open `/admin` directly while logged in as `chris`. | Page renders. |
| M-05 | Click Logout. | Cookie is cleared; further nav redirects to `/login`. |

## Tools

- pytest, httpx, freezegun (for T-08 time travel).
- Manual: any modern browser; DevTools cookie editor.

## Pass criteria

- All 12 auto tests pass on `develop` and on `main`.
- All 5 manual smoke checks pass at sprint review.
- `pytest --cov` shows ≥ 90 % line coverage on `app/auth.py` and `app/routers/auth.py`.

## Out of scope (deferred to later test specs)

- Load tests against the JWT verification path.
- Browser-driver E2E (Playwright / Cypress) — manual smoke is sufficient for v0.1.
- Penetration testing / brute-force timing attacks — out of scope per US-01 scope boundaries.
