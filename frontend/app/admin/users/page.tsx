"use client";

// UC-06 — admin user management page.
// Table: username · role · last login · tokens in/out · created · actions.
// Modals for "New user" and "Reset password"; native confirm() for delete.

import { useEffect, useMemo, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ApiError, api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

type Role = "chat" | "code" | "admin";

interface UserSummary {
  id: number;
  username: string;
  role: Role;
  must_change_password: boolean;
  created_at: string | null;
  last_login_at: string | null;
  deleted_at: string | null;
  tokens_in: number;
  tokens_out: number;
  // Sprint 7 — `is_active = 0` means the account is deactivated (login
  // blocked, sessions revoked) but not deleted. Backend defaults old
  // rows to 1; if the column happens to be missing in a snapshot we
  // treat absent-or-truthy as active.
  is_active?: number;
}

interface ApiErrorBody {
  detail?: string | { detail?: string; hint?: string };
}

const ROLES: Role[] = ["chat", "code", "admin"];

export default function AdminUsersPage() {
  const { me, loading } = useAuthStore();
  const [users, setUsers] = useState<UserSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<UserSummary | null>(null);

  // Route guard.
  useEffect(() => {
    if (loading) return;
    if (!me) {
      window.location.replace("/login/");
      return;
    }
    if (me.role !== "admin") {
      window.location.replace("/dashboard/");
    }
  }, [me, loading]);

  const refresh = useMemo(
    () => async () => {
      try {
        const list = await api<UserSummary[]>(
          `/api/admin/users${includeDeleted ? "?include_deleted=true" : ""}`,
        );
        setUsers(list);
        setError(null);
      } catch (e) {
        if (e instanceof ApiError) {
          if (e.status === 401) {
            window.location.replace("/login/");
            return;
          }
          setError(`HTTP ${e.status}: ${JSON.stringify(e.detail ?? "")}`);
        } else {
          setError(String(e));
        }
      }
    },
    [includeDeleted],
  );

  useEffect(() => {
    if (me?.role !== "admin") return;
    const id = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(id);
  }, [me, refresh]);

  if (!me || me.role !== "admin") {
    return (
      <div className="min-h-screen flex flex-col">
        <AppHeader />
        <main className="flex-1 flex items-center justify-center text-neutral-500">
          Loading…
        </main>
      </div>
    );
  }

  async function changeRole(u: UserSummary, role: Role) {
    if (role === u.role) return;
    if (
      u.role === "admin" &&
      role !== "admin" &&
      !window.confirm(`Demote ${u.username} from admin to ${role}?`)
    ) {
      return;
    }
    try {
      await api(`/api/admin/users/${u.id}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        const body = e.detail as ApiErrorBody | undefined;
        const detail =
          typeof body?.detail === "object" ? body.detail?.detail : body?.detail;
        alert(`Cannot change role: ${detail ?? `HTTP ${e.status}`}`);
      }
    }
  }

  async function revokeSessions(u: UserSummary) {
    if (
      !window.confirm(
        `Force re-login for ${u.username}? All their existing sessions will be invalidated immediately.`,
      )
    )
      return;
    try {
      await api(`/api/admin/users/${u.id}/revoke-sessions`, { method: "POST" });
      // No need to refresh — token_version isn't shown in the table.
      alert(`Sessions revoked for ${u.username}.`);
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Cannot revoke sessions: HTTP ${e.status}`);
      }
    }
  }

  async function deactivate(u: UserSummary) {
    if (
      !window.confirm(
        `Deactivate ${u.username}? Login is blocked and active sessions are revoked. You can reactivate later.`,
      )
    )
      return;
    try {
      await api(`/api/admin/users/${u.id}/deactivate`, { method: "POST" });
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        const body = e.detail as ApiErrorBody | undefined;
        const detail =
          typeof body?.detail === "object" ? body.detail?.detail : body?.detail;
        alert(`Cannot deactivate: ${detail ?? `HTTP ${e.status}`}`);
      }
    }
  }

  async function reactivate(u: UserSummary) {
    try {
      await api(`/api/admin/users/${u.id}/reactivate`, { method: "POST" });
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Cannot reactivate: HTTP ${e.status}`);
      }
    }
  }

  async function deleteUser(u: UserSummary) {
    if (u.id === me?.id) {
      alert("You cannot delete your own account.");
      return;
    }
    if (!window.confirm(`Soft-delete ${u.username}? Their conversations stay; the user can no longer log in.`))
      return;
    try {
      await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        const body = e.detail as ApiErrorBody | undefined;
        const detail =
          typeof body?.detail === "object" ? body.detail?.detail : body?.detail;
        alert(`Cannot delete: ${detail ?? `HTTP ${e.status}`}`);
      }
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background)]">
      <AppHeader />
      <main className="cockpit-page flex-1">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-neutral-900 dark:text-white">
              Users
            </h1>
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              Roles, session control, and account lifecycle.
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <label className="flex items-center gap-1 text-xs text-neutral-600 dark:text-neutral-400">
              <input
                type="checkbox"
                checked={includeDeleted}
                onChange={(e) => setIncludeDeleted(e.target.checked)}
              />
              Include deleted
            </label>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="cockpit-button cockpit-button-primary"
            >
              + New user
            </button>
          </div>
        </div>

        {error ? (
          <div className="rounded-md border border-rose-300 bg-rose-50 dark:bg-rose-950 dark:border-rose-800 text-rose-700 dark:text-rose-300 px-3 py-2 text-sm mb-4">
            {error}
          </div>
        ) : null}

        {users === null ? (
          <div className="text-neutral-500">Loading…</div>
        ) : (
          <div className="cockpit-panel overflow-x-auto">
            <table className="cockpit-table">
              <thead className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400 bg-neutral-50 dark:bg-neutral-900/50">
                <tr>
                  <th className="text-left px-3 py-2">Username</th>
                  <th className="text-left px-3 py-2">Role</th>
                  <th className="text-left px-3 py-2">Last login</th>
                  <th className="text-right px-3 py-2">Tokens in</th>
                  <th className="text-right px-3 py-2">Tokens out</th>
                  <th className="text-left px-3 py-2">Created</th>
                  <th className="text-right px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const inactive = u.is_active === 0;
                  return (
                  <tr
                    key={u.id}
                    className={`border-t border-neutral-200 dark:border-neutral-800 ${
                      u.deleted_at || inactive ? "opacity-60" : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <span className="font-mono">{u.username}</span>
                      {u.deleted_at ? (
                        <span className="ml-2 text-xs text-rose-600 dark:text-rose-400">
                          deleted
                        </span>
                      ) : null}
                      {!u.deleted_at && inactive ? (
                        <span className="ml-2 text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 bg-neutral-200 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400">
                          inactive
                        </span>
                      ) : null}
                      {u.must_change_password ? (
                        <span className="ml-2 text-xs text-amber-700 dark:text-amber-400">
                          must change pw
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={u.role}
                        disabled={!!u.deleted_at || u.id === me.id}
                        onChange={(e) => void changeRole(u, e.target.value as Role)}
                        className="rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-2 py-1 text-sm"
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2 text-neutral-600 dark:text-neutral-400">
                      {relativeTime(u.last_login_at)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-neutral-700 dark:text-neutral-300">
                      {u.tokens_in.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-neutral-700 dark:text-neutral-300">
                      {u.tokens_out.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-neutral-600 dark:text-neutral-400">
                      {relativeTime(u.created_at)}
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => setResetTarget(u)}
                        disabled={!!u.deleted_at}
                        className="text-xs rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 mr-1 hover:bg-neutral-100 dark:hover:bg-neutral-800 disabled:opacity-50"
                      >
                        Reset PW
                      </button>
                      <button
                        type="button"
                        onClick={() => void revokeSessions(u)}
                        disabled={!!u.deleted_at}
                        title="Force re-login — invalidates all outstanding sessions"
                        className="text-xs rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 mr-1 hover:bg-neutral-100 dark:hover:bg-neutral-800 disabled:opacity-50"
                      >
                        Force re-login
                      </button>
                      {inactive ? (
                        <button
                          type="button"
                          onClick={() => void reactivate(u)}
                          disabled={!!u.deleted_at}
                          className="text-xs rounded-md border border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 px-2 py-1 mr-1 hover:bg-emerald-50 dark:hover:bg-emerald-950 disabled:opacity-50"
                        >
                          Reactivate
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void deactivate(u)}
                          disabled={!!u.deleted_at || u.id === me.id}
                          className="text-xs rounded-md border border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-400 px-2 py-1 mr-1 hover:bg-amber-50 dark:hover:bg-amber-950 disabled:opacity-50"
                        >
                          Deactivate
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void deleteUser(u)}
                        disabled={!!u.deleted_at || u.id === me.id}
                        className="text-xs rounded-md border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-400 px-2 py-1 hover:bg-rose-50 dark:hover:bg-rose-950 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {createOpen ? (
          <CreateUserModal
            onClose={() => setCreateOpen(false)}
            onCreated={() => {
              setCreateOpen(false);
              void refresh();
            }}
          />
        ) : null}
        {resetTarget ? (
          <ResetPasswordModal
            user={resetTarget}
            onClose={() => setResetTarget(null)}
            onReset={() => {
              setResetTarget(null);
              void refresh();
            }}
          />
        ) : null}
      </main>
    </div>
  );
}

function CreateUserModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("chat");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ username, password, role }),
      });
      onCreated();
    } catch (err) {
      if (err instanceof ApiError) {
        const body = err.detail as ApiErrorBody | undefined;
        const detail =
          typeof body?.detail === "object" ? body.detail?.detail : body?.detail;
        setError(detail ?? `HTTP ${err.status}`);
      } else {
        setError(String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell title="New user" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          <span className="text-xs uppercase tracking-wide text-neutral-500">Username</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={2}
            maxLength={31}
            pattern="^[a-z][a-z0-9._-]{1,30}$"
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-1.5"
            autoFocus
          />
          <span className="text-xs text-neutral-500">lowercase, start with letter, [a-z0-9._-]</span>
        </label>
        <label className="block text-sm">
          <span className="text-xs uppercase tracking-wide text-neutral-500">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-1.5"
          />
          <span className="text-xs text-neutral-500">≥ 8 chars. User must change on first login.</span>
        </label>
        <label className="block text-sm">
          <span className="text-xs uppercase tracking-wide text-neutral-500">Role</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-1.5"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        {error ? <div className="text-sm text-rose-600">{error}</div> : null}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-neutral-900 dark:bg-white text-white dark:text-neutral-900 px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

function ResetPasswordModal({
  user,
  onClose,
  onReset,
}: {
  user: UserSummary;
  onClose: () => void;
  onReset: () => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api(`/api/admin/users/${user.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password: password }),
      });
      onReset();
    } catch (err) {
      if (err instanceof ApiError) {
        const body = err.detail as ApiErrorBody | undefined;
        const detail =
          typeof body?.detail === "object" ? body.detail?.detail : body?.detail;
        setError(detail ?? `HTTP ${err.status}`);
      } else {
        setError(String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell title={`Reset password — ${user.username}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-xs text-neutral-500">
          The user will be required to change this on their next login.
        </p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          autoFocus
          className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-1.5"
        />
        {error ? <div className="text-sm text-rose-600">{error}</div> : null}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-neutral-900 dark:bg-white text-white dark:text-neutral-900 px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            Reset password
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

function ModalShell({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 p-4 shadow-xl">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-neutral-500 hover:text-neutral-900 dark:hover:text-white"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// Lightweight relative-time formatter (no extra date library).
function relativeTime(isoOrNull: string | null): string {
  if (!isoOrNull) return "—";
  const then = new Date(isoOrNull);
  if (Number.isNaN(then.getTime())) return isoOrNull;
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 0) return then.toLocaleString();
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const buckets: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["second", 1],
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2592000],
    ["year", 31536000],
  ];
  for (let i = buckets.length - 1; i >= 0; i--) {
    const [unit, mult] = buckets[i];
    if (seconds >= mult) {
      const value = Math.round(seconds / mult);
      return formatter.format(-value, unit);
    }
  }
  return "just now";
}
