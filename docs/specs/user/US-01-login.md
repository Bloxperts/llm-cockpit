<!-- Status: Review | Version: 0.2 | Created: 2026-04-27 -->
# US-01 · User Spec — Family member logs in to the cockpit

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/US-01-login.md`](../functional/US-01-login.md)
**Test Spec:** [`test/US-01-login.md`](../test/US-01-login.md)
**Sprint:** 1

## Story

> As a member of the household (Chris, Mila, or a guest tester) I want to open the cockpit URL on the LAN and log in with a username and password so that I can use the dashboard and chat pages without exposing model controls to anyone outside the household network.

## Problem / current situation

Today, the only way to talk to the local Ollama models is to SSH into Neuroforge or use `curl` against `http://192.168.111.200:11434`. Non-engineering family members can't do that. Open WebUI exists but is opinionated and doesn't share auth state with the cockpit.

There is no front door. We need one that:

- Recognises the five intended users (chris, mila, lex-person, tester1, tester2) and nobody else.
- Remembers the user across browser tabs and reloads for a reasonable session length.
- Distinguishes admin from non-admin so privileged actions (model pin, `num_ctx` change) are not accessible to a guest.

## Target state from the user's perspective

I open `http://192.168.111.200:8080/` from a browser on the LAN.
I see a single screen with my username field, password field, and a Sign in button.
I type my password, hit Sign in, and within a second I land on the dashboard.
I close my browser, come back next morning, and I'm still logged in.
If I get my password wrong, the form says "Invalid credentials" — it doesn't tell me whether the username or the password was wrong.
If somebody hammers my username with bad guesses, the form starts refusing further attempts after five failures.
When I click "Log out" in the top-right corner I'm bounced back to the login screen and any further API call without my cookie is rejected.

If I'm Chris, my role is `admin` and the sidebar also shows the "Admin" tab.
If I'm anyone else, my role is `user` and the Admin tab is not visible.

## Acceptance criteria

1. The login page renders at `/login` for any unauthenticated visitor.
2. Three configured user accounts can sign in with their respective passwords (verified in Test Spec).
3. Wrong password shows "Invalid credentials" within 1 s and does not reveal whether the username or the password was wrong.
4. Six failed attempts within five minutes for one username return HTTP 429 and lock the username out for 60 s.
5. Successful login redirects to `/dashboard` and sets an `HttpOnly`, `SameSite=Strict` cookie containing a JWT.
6. JWT lifetime is 7 days. Any authenticated request issued more than 1 day before expiry causes the response to refresh the cookie.
7. Visiting any other page without a valid JWT redirects to `/login`.
8. Logout endpoint clears the cookie; subsequent API calls return 401.
9. Every login attempt (success and failure) writes one row to a `login_audit` table with `ts`, `username`, `success`, `source_ip`.
10. The Admin tab in the sidebar appears only when the JWT carries `role=admin`.

## Scope boundaries (out)

- 2FA / TOTP — not in v0.1.
- OAuth, SAML, OpenID Connect, single sign-on — not in v0.1.
- Self-service registration — not in v0.1.
- Password recovery flow over email — not in v0.1.
- Per-user MFA settings UI — not in v0.1.
- Multi-tenant orgs — not in v0.1.

User provisioning is **CLI-only** (`cockpit-admin user-add …`). Adding a sixth user requires Chris on the host.

## Notes

- This story is a hard prerequisite for US-02, US-04, US-05 — none of those pages render without a valid session.
- DP-031 (Progressive Autonomy) applies indirectly: the admin role gate is the simplest realisation of "user actions before admin actions".
- DP-032 (Privacy Tiers): per-user chat history that lands in v0.4 must already be keyed by the JWT subject; this story sets up the subject identifier.
