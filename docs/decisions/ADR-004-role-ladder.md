<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 -->
# ADR-004 · Role ladder + permission model

**Status:** Accepted
**Date:** 2026-04-27
**Supersedes:** —
**Related:** ADR-003 §3 (single admin seed) and §6 (admin scope shrunk to user management).

## Context

The cockpit is multi-user. Different users need different surfaces:

- A casual user opens the chat page; they should never see admin tabs.
- A developer also wants the code page (different model picker, code-emphasis rendering).
- The admin needs user management *and* — per Chris's clarification on 2026-04-27 — Ollama configuration, metrics, and audit log access.

Two shapes were considered:

1. **Capability flags.** Each user has a set of capabilities — `chat`, `code`, `admin` — selected independently. A user could be `chat=true, code=false, admin=false` or `chat=true, code=true, admin=false` etc.
2. **Single role on a ladder.** Each user has exactly one role. Roles are ordered: `chat < code < admin`. Higher roles include all lower-rung capabilities.

Flags are more flexible (a user could be `code` without `chat`, in principle). The ladder is simpler — fewer states, fewer error cases, clearer mental model. Chris's preference was for the ladder ("Probably it is ladder up that way").

A small but important second decision is how the cockpit knows which models are "chat" vs "code", since Ollama itself does not surface that distinction.

## Decision

### 1. Role ladder, single role per user

Three roles, in ascending order of capability:

```
chat  <  code  <  admin
```

Each user has exactly one role. Higher roles include every lower-rung capability — there is no "code without chat" state.

| Role | Capabilities |
|------|--------------|
| `chat` | Log in. Use `/chat`. See and resume own chat conversations. Read own profile. Change own password. |
| `code` | Above. Plus use `/code`. See and resume own code conversations. |
| `admin` | Above. Plus access `/admin/users` (US-06) and `/admin/ollama` (US-10). See system-wide metrics and audit logs. |

Internal storage: `users.role TEXT NOT NULL CHECK (role IN ('chat', 'code', 'admin'))`. UI labels: "Chat user", "Code user", "Admin".

### 2. Default role for new users

- **Bootstrap admin** (`admin` / `ollama` from ADR-003 §3): role `admin`.
- **Admin-created users** (US-06): admin picks the role at creation time. Default selection in the form is `chat` (least-privileged).
- **No self-registration** in v0.1.

### 3. How `chat` vs `code` model classification works

Ollama does not tell us which models are "code" models. The cockpit decides, so the chat picker shows chat models and the code picker shows code models.

Two layers:

1. **Heuristic auto-tag** at model-discovery time. Regex on the model name; if the name contains any of `coder`, `code-`, `codellama`, `deepseek-r1`, `qwen2.5-coder`, `starcoder`, `wizardcoder`, `phind`, `magicoder`, the model is auto-tagged `code`. Everything else is auto-tagged `chat`. The exact regex list lives in `config/model_tag_heuristics.yaml` and is editable by the admin without restarting.
2. **Admin override** (US-10). The Ollama-config admin page shows every model Ollama is serving with its current tag (`chat` / `code` / `both`). Admin can override per model. Override wins over heuristic. Override is persisted in `model_tags(model TEXT PRIMARY KEY, tag TEXT NOT NULL)`.

A model tagged `both` appears in both pickers.

### 4. Authorization layer

A FastAPI dependency `require_role(min_role)` is the single enforcement point. It:

1. Resolves the JWT to a user.
2. Compares `user.role` against `min_role` on the ladder.
3. Returns 403 (not 401) if the user is logged in but under-privileged.

```python
def require_role(min_role: Role):
    rank = {"chat": 0, "code": 1, "admin": 2}
    def dep(user: User = Depends(current_user)):
        if rank[user.role] < rank[min_role]:
            raise HTTPException(403, "insufficient role")
        return user
    return dep
```

Routes use it like:

```
@router.post("/api/chat/...")        Depends(require_role("chat"))
@router.post("/api/code/...")        Depends(require_role("code"))
@router.get ("/api/admin/users")     Depends(require_role("admin"))
@router.get ("/api/admin/ollama/*")  Depends(require_role("admin"))
```

Frontend mirrors this: the sidebar hides the Admin and Code links for users without the role. The hide is cosmetic — the backend is the only enforcement point.

### 5. Role changes propagate immediately

When an admin changes a user's role (US-06), the next request that user makes — even with an existing valid JWT — reflects the new role. We achieve this by *not* baking the role into the JWT claims: the JWT only carries `sub` (user id). The role is resolved from the `users` table at every request via the `current_user` dependency. Cost is one indexed row read per request, which is negligible at five-user scale.

## Consequences

**Positive**

- One column, three values, full ordering. Tests are simple.
- New role can be inserted on the ladder later (e.g. `read-only` below `chat`, or `support` between `code` and `admin`) without breaking existing rows.
- Frontend uses one helper (`hasAtLeast(role)`) for every gate.
- Role flips take effect immediately — useful when an admin demotes someone abusing access.

**Negative**

- A user who genuinely wants `code` without `chat` cannot exist. Acceptable for v0.1; revisit if real usage proves otherwise.
- Heuristic-based model tagging will misclassify some models; admin override is the safety valve.

**Neutral**

- The bootstrap admin's role is `admin` per ADR-003 §3. No change.

## Compliance

- DP-007 (simplicity over elegance) — explicitly applied. Ladder beats flags here.
- DP-031 (progressive autonomy) — the ladder *is* progressive autonomy applied to the human side (each rung enables more action).
- DP-032 (privacy tiers) — chat history is private to the user; admin sees aggregate metrics, not message contents (US-10 enforces this in the metrics view).
- DP-029 (hexagonal) — the `require_role` dependency is the inbound port for authorization; there is no outbound dependency for authorization in v0.1 (no LDAP/OIDC adapter).

## Follow-up

- US-01 (login) emits a JWT carrying only `sub`; the `current_user` dependency resolves the role at request time.
- US-06 (user management) supports setting role to any of the three.
- US-10 (Ollama configuration) supports per-model tag override and exposes the heuristic config.
- A future `read-only` role (below `chat`) is plausible if a "give my parents a metrics-only view" use case appears. Out of scope for v0.1.
