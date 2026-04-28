# Claude Code prompt — Sprint 2 MVP: UC-08 Slice B + UC-01 + UC-09

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state right now

- `origin/develop` includes UC-08 Slice A **and** UC-07 (just merged, PR #2).
  `feature/sprint2-mvp` already exists and has been rebased onto the updated develop.
  **You are already on the correct branch. Do not create a new branch.**
- Zero code has been written on `feature/sprint2-mvp` yet.

## Goal

By the end of this session Chris must be able to run:

```
pip install -e .
cockpit-admin init
cockpit-admin serve
```

Open a browser, log in as `admin` / `ollama`, be forced to change the password,
and land on an empty dashboard placeholder. That is the Sprint 2 done condition.

---

## Read first (before writing a single line)

1. `CLAUDE.md` — rules, stack, Spec-First gate, branch/commit conventions.
2. `docs/process/SPRINT_STATE.md`
3. `docs/specs/functional/UC-08-installation-bootstrap.md` — `serve` flow, `main.py` shape.
4. `docs/specs/functional/UC-01-login.md` — auth API, JWT, rate limit, audit.
5. `docs/specs/functional/UC-09-first-login-password-change.md` — force-change dep + endpoint.
6. `docs/specs/test/UC-08-installation-bootstrap.md`
7. `docs/specs/test/UC-01-login.md`
8. `docs/specs/test/UC-09-first-login-password-change.md`

---

## What already exists on develop — do not rebuild

- `src/cockpit/models.py` — all six ORM tables, including `users.must_change_password`,
  `users.password_changed_at`, `users.last_login_at`, `login_audit.action`. No migration needed.
- `src/cockpit/ports/llm_chat.py` — `LLMChat` Protocol, dataclasses, exception hierarchy (UC-07).
- `src/cockpit/adapters/ollama_chat.py` — `OllamaLLMChat`, all five methods (UC-07).
- `src/cockpit/adapters/fake_chat.py` — `FakeLLMChat` + `model_info` factory (UC-07).
- `src/cockpit/services/bootstrap.py` — UC-07 DI-refactored: `probe_ollama` and `run_init`
  accept `chat_factory`; no raw `httpx` call outside `adapters/`.
- `src/cockpit/cli.py` — `init`, `migrate`, `doctor` working; `cmd_serve` is still a stub
  (exit code 2). That stub is what you are replacing.
- `pyproject.toml` — dependencies already include `fastapi`, `uvicorn[standard]`, `bcrypt`,
  `python-jose[cryptography]`, `sse-starlette`, `pydantic`.

---

## Branching and commit discipline

Branch already created: `feature/sprint2-mvp`. Make **one commit per UC**:

```
[UC-08] cockpit-admin serve + FastAPI main.py (Slice B)
[UC-01] auth router: login, JWT, current_user, require_role
[UC-09] forced password change: endpoint + dep + frontend pages
```

At the end open **three separate PRs** against `develop`, each scoped to its UC commit.
Chris reviews and merges after local smoke passes.

---

## Role discrepancy in UC-01 spec — follow models.py, not the spec

The UC-01 spec's data model block shows `role IN ('admin','user')`. That predates ADR-004.
The live `models.py` has `role IN ('chat', 'code', 'admin')`. Follow models.py / ADR-004
everywhere. This is a known stale fragment, not an open question — do not stop on it.

---

## UC-08 Slice B — `cockpit-admin serve` + `main.py`

### Files to create / modify

**`src/cockpit/main.py`** — FastAPI app factory:

```python
def create_app(settings: CockpitSettings | None = None) -> FastAPI:
    ...
```

- Registers `routers/auth.py` under prefix `/api/auth`.
- Mounts `frontend_dist/` as `StaticFiles(html=True)` for any path not under `/api`.
  Resolve `frontend_dist` relative to the installed package so it works after `pip install`.
- On startup: probe Ollama once via `OllamaLLMChat(url).list_models()`, log a WARNING
  if unreachable, **do not exit** (spec §serve flow bullet 4).
- On startup: auto-run `upgrade_to_head()` if DB schema is behind.

**`src/cockpit/schemas.py`** — Pydantic v2 request/response models used by UC-01 and UC-09:
`LoginRequest`, `LoginResponse`, `MeResponse`, `ChangePasswordRequest`.

**`src/cockpit/cli.py`** — replace the `cmd_serve_stub` with the real `cmd_serve`:

```python
def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn
    from cockpit.main import create_app
    app = create_app(...)
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())
```

Load host/port from `config.toml`; CLI flags `--host` / `--port` override.

**`src/cockpit/frontend_dist/`** — minimal static HTML placeholder (see §Frontend below).

### Tests — `tests/test_uc08_serve.py`

`TestClient` confirms:
- App starts without error.
- `/api/auth/me` without cookie → 401.
- `GET /` serves the index HTML (StaticFiles working).
- `GET /dashboard` serves the dashboard HTML.
- Existing 44-test UC-08 suite stays green.

---

## UC-01 — Auth router

### `src/cockpit/routers/auth.py`

**`POST /api/auth/login`**
- Validate credentials against bcrypt hash.
- On success: issue JWT (HS256, `sub=str(user.id)`, `exp=now+7d`), set cookie
  `cockpit_jwt` with `httponly=True`, `samesite="strict"`, `path="/"`.
- On failure: 401 `{"detail": "Invalid credentials"}`.
- Rate limit: in-memory per-username counter; 5 failures in 5 min → 429
  `{"detail": "too_many_attempts", "retry_after_seconds": 60}`.
  A `dict[str, list[float]]` of timestamps is sufficient.
- Write `login_audit` row for every attempt. Update `users.last_login_at` on success.

**`GET /api/auth/me`**
- Requires valid JWT cookie. Returns `{id, username, role, must_change_password}`.
- Uses `current_user` only (not `current_user_must_be_settled`) so the
  frontend can always read this flag.

**`POST /api/auth/logout`**
- Clears the cookie. Requires `current_user_must_be_settled` (demonstrates the
  dependency is wired; guards against a stale-tab logout on an unsettled account).

**Sliding JWT renewal** — on any authenticated call, if the token is < 1 day from
expiry, re-issue and set a fresh cookie in the response.

**`current_user` dependency** — decode JWT from cookie, look up `User` by `sub` in DB.
Raises `HTTPException(401)` on missing/invalid/expired token or unknown user.
Role is resolved from DB, not from the JWT payload.

**`require_role(min_role)` dependency** — exactly as in CLAUDE.md:

```python
def require_role(min_role: str):
    rank = {"chat": 0, "code": 1, "admin": 2}
    def dep(user: User = Depends(current_user)):
        if rank[user.role] < rank[min_role]:
            raise HTTPException(403, "insufficient role")
        return user
    return dep
```

**`services/users.py` additions** — add `get_user_by_id(session, user_id)` and
`verify_password(plain, pw_hash)` if not already there.

### Tests — `tests/test_uc01_auth.py`

Cover every AC in the spec:
- Correct credentials → 200, `cockpit_jwt` cookie set with `httponly` + `samesite=strict`.
- Wrong credentials → 401 "Invalid credentials".
- Six bad attempts in sequence → 429 on the seventh.
- `/api/auth/me` without cookie → 401.
- `/api/auth/me` with valid cookie → 200 with `must_change_password` field.
- Logout → `cockpit_jwt` cookie cleared.
- `login_audit` row exists for every attempt (success + fail).
- `require_role`: correct role passes; insufficient role → 403.

Use `TestClient` + in-memory SQLite fixture. Inject `FakeLLMChat` for any
startup probe so no real Ollama is needed.

---

## UC-09 — Forced password change

### Additions to `src/cockpit/routers/auth.py`

**`current_user_must_be_settled` dependency:**

```python
def current_user_must_be_settled(user: User = Depends(current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(
            409,
            detail="must_change_password",
            headers={"WWW-Authenticate": "ChangePassword"},
        )
    return user
```

Apply to every protected route except `POST /api/auth/change-password` and
`GET /api/auth/me`. For Sprint 2 that means `POST /api/auth/logout`.

**`POST /api/auth/change-password`** — uses `current_user` only.

Validation (server-side):
- `new_password == confirm_password` → else 400 `{"detail": "passwords_dont_match"}`.
- `len(new_password) >= 8` → else 400 `{"detail": "too_short", "min": 8}`.
- `new_password != "ollama"` → else 400 `{"detail": "cannot_reuse_default"}`.

On success:
- Bcrypt-hash the new password.
- `UPDATE users SET pw_hash=?, must_change_password=0, password_changed_at=now()`.
- Write `login_audit(action='password_changed', success=1, source_ip=...)`.
- Return 200 `{}`.

### Tests — `tests/test_uc09_change_password.py`

- All three validation failures return correct 400 detail strings.
- Successful change clears `must_change_password` and sets `password_changed_at`.
- `login_audit` row written with `action='password_changed'`.
- Hitting `POST /api/auth/logout` with `must_change_password=1` → 409 with
  `WWW-Authenticate: ChangePassword` header.
- Hitting `GET /api/auth/me` with `must_change_password=1` → 200 (not blocked).

---

## Frontend — minimal placeholder (not Next.js)

The full Next.js frontend is Sprint 4. For Sprint 2, place four plain HTML files
in `src/cockpit/frontend_dist/`. FastAPI's `StaticFiles(html=True)` serves them.

**`index.html`** — checks for `cockpit_jwt` cookie: if present redirect to
`/dashboard`, else redirect to `/login`. Inline `<script>`, no framework.

**`login/index.html`** — username + password form. On submit: `POST /api/auth/login`
→ on 200 call `GET /api/auth/me` → if `must_change_password` redirect to
`/change-password`, else redirect to `/dashboard`. On 401 show "Invalid credentials".
On 429 show "Too many attempts — wait 60 s".

**`change-password/index.html`** — two password fields + submit.
`POST /api/auth/change-password` → on 200 redirect to `/dashboard`. Show field-level
errors from `detail`. Intercept 401 → redirect to `/login`.

**`dashboard/index.html`** — heading "LLM Cockpit — Dashboard (Sprint 2 placeholder)".
On load call `GET /api/auth/me`: if 401 redirect to `/login`; if 409 redirect to
`/change-password`; else display username + role. Logout button: `POST /api/auth/logout`
→ redirect to `/login`.

Plain HTML + inline vanilla JS only. No external dependencies. Sprint 4 replaces
these entirely.

---

## Spec status edits (vault not mounted — fallback rule)

For each UC as you implement it:
- Flip its functional spec header `Accepted → In Progress` when you start, `Done (technical)`
  when tests pass.
- Fill in any stub test spec bodies; add `<!-- VAULT-SYNC -->` comments for sprint-review
  mirroring.

---

## Coverage target

```
pytest --cov=cockpit.main --cov=cockpit.routers.auth \
       --cov=cockpit.services.users --cov=cockpit.schemas \
       --cov-report=term-missing
```

≥ 90 % on each touched module. All prior tests (UC-07, UC-08 Slice A) must stay green.

---

## Stop and ask Chris if

- Any spec says something FastAPI / jose / bcrypt doesn't support as written.
- A new dependency is required beyond what's already in `pyproject.toml`.
- An AC is genuinely ambiguous after reading both the functional and test specs.
