"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Me, useAuthStore } from "@/lib/auth-store";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const setMe = useAuthStore((s) => s.setMe);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      const me = await api<Me>("/api/auth/me");
      setMe(me);
      if (me.must_change_password) {
        window.location.replace("/change-password/");
      } else {
        window.location.replace("/dashboard/");
      }
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 401) setError("Invalid credentials");
        else if (e.status === 429) {
          const detail = (e.detail as { detail?: { retry_after_seconds?: number } })?.detail;
          const retry = detail?.retry_after_seconds ?? 60;
          setError(`Too many attempts — wait ${retry} s and try again.`);
        } else {
          setError(`Unexpected error (${e.status}).`);
        }
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
        className="w-full max-w-sm space-y-4 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold">LLM Cockpit — Sign in</h1>
        <label className="block">
          <span className="text-sm font-medium">Username</span>
          <input
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-2"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Password</span>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-2"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 text-white px-4 py-2 font-medium disabled:opacity-60"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        {error ? (
          <p className="text-sm text-rose-600" role="alert" aria-live="polite">
            {error}
          </p>
        ) : null}
      </form>
    </main>
  );
}
