<!-- Status: Accepted | Version: 0.3 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-01 · User Spec — User logs in to the cockpit

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`functional/UC-01-login.md`](../specs/functional/UC-01-login.md)
**Test Spec:** [`test/UC-01-login.md`](../specs/test/UC-01-login.md)
**Sprint:** 2
**Related:** UC-08 (installation seeds the first admin), UC-09 (first-login forced password change), ADR-004 (role ladder).

## Story

> As a registered user — `chat`, `code`, or `admin` — I want to open the cockpit URL in my browser, type my username and password, and land on a page that matches my role, so that the cockpit is immediately useful and admins don't have to coach me through configuration.

## Problem / current situation

There is no front door yet. To use Ollama you SSH to the host and curl `:11434`, or you boot Open WebUI which is a separate identity surface. We need:

- A login screen for the cockpit.
- A session that survives reload, with sensible idle/expiry behaviour.
- A role-aware first page after login (chat user → `/chat`, code user → `/chat` (default) or `/code` (last-used), admin → `/dashboard`).
- A clear story for "I forgot whether my password was wrong" without leaking which field was at fault.

## Target state from the user's perspective

I open `http://<host>:8080/` from any device on the LAN.
I see a single screen with my username field, password field, and a "Sign in" button.
I type my password, press Sign in, and within one second I'm on the right landing page for my role.
I close the browser. Tomorrow morning I open it again and I'm still logged in.
If my password is wrong, the form says "Invalid credentials" — it does not tell me whether the username or the password was the problem.
If somebody hammers my username with bad guesses, the form starts refusing further attempts after five failures within five minutes.
If I am the seeded `admin` (UC-08) or any user the admin created (UC-06), I am required to change my password before I can do anything else (UC-09).
When I click "Log out" in the top-right, I'm bounced back to the login screen and any further API call without my cookie is rejected.

## Acceptance criteria

1. The login page renders at `/login` for any unauthenticated visitor.
2. Three roles successfully log in (one user per role: `admin`, `code`, `chat`) and land on:
   - `admin` → `/dashboard`.
   - `code` → `/code` if the user has previously used Code, else `/chat`.
   - `chat` → `/chat`.
3. Wrong password shows "Invalid credentials" within 1 s and does not reveal whether the username or the password was wrong.
4. Six failed attempts within five minutes for one username return HTTP 429 and lock the username out for 60 s.
5. Successful login sets an `HttpOnly`, `SameSite=Strict` cookie containing a JWT.
6. JWT lifetime is 7 days. Any authenticated request issued more than 1 day before expiry causes the response to refresh the cookie.
7. Visiting any protected page without a valid JWT redirects to `/login`.
8. Logout endpoint clears the cookie; subsequent API calls return 401.
9. Every login attempt (success and failure) writes one row to a `login_audit` table with `ts`, `username`, `success`, `source_ip`.
10. JWT carries only `sub` (user id). Role is resolved at every request from the `users` table per ADR-004 §5, so role flips by the admin take effect immediately.
11. If `users.must_change_password = 1` for the logged-in user, every protected route except `/api/auth/change-password` returns 409 with body `{"detail": "must_change_password"}`. The frontend renders a forced password-change screen. (Owned by UC-09; cross-referenced here for completeness.)

## Scope boundaries (out)

- 2FA / TOTP — not in v0.1.
- OAuth, SAML, OIDC, single sign-on — not in v0.1.
- Self-service registration — not in v0.1 (admin creates users; UC-06).
- Password recovery flow over email — not in v0.1.
- Per-user MFA settings — not in v0.1.
- Multi-tenant orgs — not in v0.1.

## Notes

- Hard prerequisite for UC-02, UC-03, UC-04, UC-05, UC-06, UC-10 — none of those pages render without a session.
- The single source of truth for role is the `users` table. Do **not** bake role into JWT claims (ADR-004 §5).
- `chat` users seeing the Admin or Code tabs in the sidebar is a UI bug, not a security issue — the backend gate is the real one.
