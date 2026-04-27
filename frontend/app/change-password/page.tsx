"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Me, useAuthStore } from "@/lib/auth-store";

const MESSAGES: Record<string, string> = {
  passwords_dont_match: "The two passwords do not match.",
  too_short: "Password must be at least 8 characters.",
  cannot_reuse_default: "You cannot reuse the default password 'ollama'.",
};

export default function ChangePasswordPage() {
  const { me, loading } = useAuthStore();
  const setMe = useAuthStore((s) => s.setMe);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (loading) return;
    if (!me) {
      window.location.replace("/login/");
      return;
    }
    if (!me.must_change_password) {
      window.location.replace("/dashboard/");
    }
  }, [me, loading]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError(MESSAGES.passwords_dont_match);
      return;
    }
    setSubmitting(true);
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          new_password: newPassword,
          confirm_password: confirmPassword,
        }),
      });
      // Refetch /me — the must_change_password flag is now false.
      const fresh = await api<Me>("/api/auth/me");
      setMe(fresh);
      window.location.replace("/dashboard/");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 401) {
          window.location.replace("/login/");
          return;
        }
        const detail = typeof e.detail === "object" && e.detail !== null
          ? (e.detail as { detail?: string }).detail
          : null;
        const code = typeof detail === "string" ? detail : "unknown_error";
        setError(MESSAGES[code] ?? `Error: ${code}`);
      } else {
        setError("Network error.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex-1 flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-md space-y-4 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold">Change your password</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          You must set a new password before you can use the cockpit.
        </p>
        <label className="block">
          <span className="text-sm font-medium">New password</span>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-2"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            minLength={8}
            autoComplete="new-password"
            autoFocus
            required
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Confirm new password</span>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-2"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            minLength={8}
            autoComplete="new-password"
            required
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 text-white px-4 py-2 font-medium disabled:opacity-60"
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
        {error ? (
          <p className="text-sm text-rose-600" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </main>
  );
}
