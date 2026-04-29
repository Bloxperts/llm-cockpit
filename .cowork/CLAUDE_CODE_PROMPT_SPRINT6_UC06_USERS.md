# Claude Code prompt — Sprint 6: UC-06 User management + Code working folder

Paste this verbatim into a Claude Code session rooted at the `llm-cockpit` repo.

---

## Repo state

`develop` includes the Sprint 5 / Sprint 5b stack. Confirm:

```bash
git fetch origin
git log origin/develop --oneline -6
```

---

## Read first (before writing a single line)

1. `CLAUDE.md` — rules, Spec-First gate, branch/commit conventions.
2. `docs/process/SPRINT_STATE.md` — confirm Sprint 6 is open.
3. `docs/specs/functional/UC-06-admin-controls.md` — **must be Accepted**. This is
   your primary reference for user management endpoints.
4. `src/cockpit/models.py` — `User`, `Conversation`, `Message`, `AdminAudit` tables.
5. `src/cockpit/routers/admin_ollama.py` — pattern to follow for admin endpoints.
6. `src/cockpit/services/audit.py` — `write_admin_audit()` helper.

---

## Branch

```bash
git checkout develop && git pull
git checkout -b feature/UC-06-user-management
```

Commit prefix: `[UC-06]`. One PR against `develop` when done.

---

## What already exists — do not rebuild

- `src/cockpit/models.py` — `User` (has `last_login_at`, `created_at`, `deleted_at`),
  `Conversation` (has `user_id`), `Message` (has `usage_in`, `usage_out`),
  `AdminAudit`, `Settings`.
- `src/cockpit/services/audit.py` — `write_admin_audit()`.
- `src/cockpit/deps.py` — `current_user`, `require_role`, `require_role_settled`,
  `get_session`, `get_settings`.
- `src/cockpit/services/users.py` — `get_user_by_id`, `verify_password`, and likely
  some bootstrap helpers.

---

## Part A — User management backend (UC-06 spec)

Create **`src/cockpit/routers/admin_users.py`**.

All endpoints require `Depends(require_role_settled("admin"))`.

### Schemas (add to `src/cockpit/schemas.py`)

```python
class UserSummary(BaseModel):
    id: int
    username: str
    role: str                           # chat | code | admin
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None
    deleted_at: datetime | None         # non-null = soft-deleted
    tokens_in: int                      # lifetime usage_in sum
    tokens_out: int                     # lifetime usage_out sum

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "chat"

class PatchRoleRequest(BaseModel):
    role: str

class ResetPasswordRequest(BaseModel):
    new_password: str
```

### Token aggregation query

`tokens_in` and `tokens_out` in `UserSummary` come from:

```sql
SELECT COALESCE(SUM(m.usage_in), 0), COALESCE(SUM(m.usage_out), 0)
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.user_id = :user_id
  AND m.role = 'assistant'
```

Build a helper in `src/cockpit/services/users.py`:

```python
def get_token_totals(session: Session, user_id: int) -> tuple[int, int]:
    """Return (usage_in_total, usage_out_total) for a user across all conversations."""
```

Use this helper when building the `UserSummary` list.

### Endpoints

**`GET /api/admin/users`**
- Returns `list[UserSummary]`.
- Includes token totals for each user (batch the aggregation — one query with GROUP BY
  rather than N+1 per user):

  ```sql
  SELECT c.user_id,
         COALESCE(SUM(m.usage_in), 0)  AS tokens_in,
         COALESCE(SUM(m.usage_out), 0) AS tokens_out
  FROM conversations c
  JOIN messages m ON m.conversation_id = c.id
  WHERE m.role = 'assistant'
  GROUP BY c.user_id
  ```

- Filter: `WHERE users.deleted_at IS NULL` by default. Accept `?include_deleted=true`
  to include soft-deleted users (admin auditing).
- Accept `?q=` for username prefix search.

**`POST /api/admin/users`** — create user
- Validate username regex `^[a-z][a-z0-9._-]{1,30}$`.
- Check uniqueness.
- Bcrypt-hash the password.
- `must_change_password = True` (spec default for admin-created accounts).
- Write `AdminAudit(action='user_created', target_model=None, details={'username', 'role'})`.
- Return `UserSummary` for the new user (tokens will be 0).

**`PATCH /api/admin/users/{id}/role`** — change role
- Cannot demote last admin (`SELECT COUNT(*) WHERE role='admin' AND deleted_at IS NULL`).
- Write audit `action='role_changed'`.
- Return updated `UserSummary`.

**`POST /api/admin/users/{id}/reset-password`** — admin password reset
- Validates `len(new_password) >= 8`.
- Bcrypt-hash, set `must_change_password = True`.
- Write audit `action='password_reset_by_admin'`.
- Return `{"ok": True}`.

**`DELETE /api/admin/users/{id}`** — soft delete
- Cannot self-delete (`actor_id == target_id` → 400).
- Cannot delete the last admin.
- Sets `deleted_at = now()`.
- Write audit `action='user_deleted'`.
- Return 204.

Register router in `src/cockpit/main.py` under prefix `/api/admin/users`.

### Tests — `tests/test_uc06_users.py`

Cover every AC in the UC-06 spec plus:
- `GET /api/admin/users` returns `tokens_in` / `tokens_out` aggregated correctly
  (seed two conversations + messages for a test user, verify totals).
- `GET /api/admin/users` returns `last_login_at` from the `users` row.
- Self-delete → 400.
- Last-admin demotion → 400.

---

## Part B — Code working folder

### Background + recommendation

Chris wants a per-user working directory where code files live and are accessible
from the Code page. Since llm-cockpit is local-first (single server on LAN), the
right approach is:

1. **Server-side filesystem folder** at `{COCKPIT_DATA_DIR}/code_files/{username}/`
   — created on first access, cleaned up with the user on hard-delete.
2. **REST file browser API** — list, download, delete (and optionally upload).
   The LLM-generated file artifacts (from the "Save to workspace" button in the
   Code UI) POST to this API. The user downloads files from the Code sidebar.
3. **Code page sidebar file tree** — replaces or augments the conversation list
   with a tab "Files" that shows the working folder contents and allows one-click
   download / delete.

This avoids any SFTP/SCP complexity while keeping all files in a well-known place
on the Neuroforge filesystem that the admin can also access directly.

### Backend — `src/cockpit/routers/code_files.py`

All endpoints require `Depends(require_role_settled("code"))` (code + admin roles).

**Settings** — add to `src/cockpit/config.py` `CockpitSettings`:

```python
code_files_dir: Path = Field(default=None)  # None → {data_dir}/code_files/

@property
def resolved_code_files_dir(self) -> Path:
    return self.code_files_dir or (self.data_dir / "code_files")
```

**Security helper** — path-traversal guard:

```python
def safe_user_path(base: Path, username: str, rel: str) -> Path:
    """Resolve rel inside base/username/. Raises 400 if it escapes."""
    user_root = (base / username).resolve()
    target = (user_root / rel).resolve()
    if not str(target).startswith(str(user_root)):
        raise HTTPException(400, "invalid path")
    return target
```

**`GET /api/code/files`** — list files
- Returns `list[FileEntry]`:
  ```python
  class FileEntry(BaseModel):
      name: str
      path: str        # relative to user root, URL-safe
      size_bytes: int
      modified_at: datetime
      is_dir: bool
  ```
- Non-recursive by default. Accept `?dir=subpath` to list a subdirectory.
- Creates the user's folder on first call (mkdir -p).

**`GET /api/code/files/download`** — download a file
- Query param `?path=filename.py`.
- Returns `FileResponse` with correct `Content-Disposition`.

**`POST /api/code/files/save`** — save a file (from chat artifact)
- Body: `{ "path": "report.html", "content": "...", "overwrite": false }`.
- If `overwrite=false` and file exists → 409 with `{"detail": "file_exists"}`.
- Writes atomically (write to `.tmp` then rename).
- Returns `FileEntry` for the saved file.

**`DELETE /api/code/files`** — delete a file
- Query param `?path=filename.py`.
- Returns 204.

Register under `/api/code/files` in `main.py` (after the existing code router).

### Frontend — Code page file drawer

In `frontend/src/app/code/page.tsx` (or `ChatShell.tsx` when `mode === 'code'`):

Add a **"Files"** tab or panel in the sidebar (below or alongside the conversation
list):
- Fetch `GET /api/code/files` on mount and after each save.
- Render a flat list (no deep tree needed for v0.1):
  ```
  📄 analysis.py      4.2 KB   [↓ Download] [🗑 Delete]
  📄 report.html      18 KB    [↓ Download] [🗑 Delete]
  ```
- Download button → `GET /api/code/files/download?path=...` via `<a download>`.
- Delete button → `DELETE /api/code/files?path=...` → refresh list.

**"Save to workspace" button** — in the code block renderer (Feature 2 from Sprint 5
added a Download button; extend it):
- When `mode === 'code'`, add a third button: **Save** (cloud-upload icon).
- On click: POST to `/api/code/files/save` with `{ path: "artifact.{ext}", content }`.
  - If 409 (file exists): prompt user "Overwrite or rename?" (simple `window.prompt`
    for the filename is fine for v0.1).
  - On success: show a brief toast/flash "Saved to workspace" and refresh the file list.

### Tests — `tests/test_code_files.py`

- List on empty dir → `[]` (creates the folder).
- Save + list → file appears with correct size.
- Download → response has correct Content-Disposition.
- Delete → file disappears from list.
- Path traversal (`../../../etc/passwd`) → 400.
- Save with `overwrite=false` when file exists → 409.

---

## Frontend — Admin users page

Create `frontend/src/app/admin/users/page.tsx`:

- Gate: `role === 'admin'` only; redirect non-admins to `/dashboard`.
- Table columns:
  `Username | Role | Last login | Tokens in | Tokens out | Created | Actions`
- Last login: format as relative time (`2 hours ago`, `3 days ago` — use a small
  helper or `Intl.RelativeTimeFormat`; no new date library needed).
- Token columns: formatted with `toLocaleString()` for thousands separators.
- Actions: **Change role** (dropdown), **Reset password** (opens a modal with a
  `<input type="password">` field), **Delete** (confirmation dialog).
- "New user" button at top: opens a modal with username + password + role fields.
- Add a link to `/admin/users` in `AppHeader.tsx` (visible to admin only).

---

## Spec status edits

- `docs/specs/functional/UC-06-admin-controls.md`: flip `Accepted → In Progress`.

---

## Coverage target

```bash
pytest --cov=cockpit.routers.admin_users \
       --cov=cockpit.routers.code_files \
       --cov=cockpit.services.users \
       --cov-report=term-missing
```

≥ 90 % on each new module.

---

## Build + release

```bash
make build

gh pr create \
  --base develop \
  --head feature/UC-06-user-management \
  --title "[UC-06] User management + code working folder" \
  --body "UC-06 user management backend + frontend:
- GET/POST/PATCH/DELETE /api/admin/users with last_login_at + lifetime token totals
- /admin/users page: table with role editor, password reset, soft delete, new user modal
- Code working folder: per-user filesystem dir, REST file browser API (list/download/save/delete), file drawer in Code page sidebar, Save-to-workspace button on code blocks
- Path-traversal guard on all file operations"

gh pr merge --squash \
  --subject "[UC-06] User management + code working folder" \
  --delete-branch=false

git checkout develop && git pull
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 dist/llm_cockpit-0.2.0-py3-none-any.whl \
  --title "v0.2.0 — User management + code workspace" \
  --notes "**User management (UC-06)**
- /admin/users page: create users, change roles, reset passwords, soft delete
- User list shows last login time and lifetime input/output token totals

**Code working folder**
- Per-user workspace at {data_dir}/code_files/{username}/
- File browser panel in the Code page sidebar (list, download, delete)
- Save-to-workspace button on code artifacts — one click to persist LLM output to the server
- All file operations path-traversal-guarded"
```

Note: bump to `v0.2.0` (minor version) because this adds a user-visible management
surface. Update `pyproject.toml` version to `0.2.0` before building.

---

## Stop and ask Chris if

- The UC-06 functional spec is not yet `Accepted` — stop, do not implement.
- A `code_files_dir` override is needed in `config.toml` beyond the default
  `{data_dir}/code_files/` — ask Chris before making it configurable.
- The file list should be recursive (subdirectory tree) vs flat — default flat for v0.1.
