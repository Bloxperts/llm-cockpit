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
    <main className="flex-1 flex items-center justify-center px-4 py-10">
      <form
        onSubmit={onSubmit}
        className="cockpit-panel w-full max-w-sm space-y-4 p-8"
      >
        <div>
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-neutral-950 font-mono text-sm font-semibold text-white dark:bg-white dark:text-neutral-950">
            LC
          </div>
          <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">LLM Cockpit</p>
        </div>
        <label className="block">
          <span className="text-sm font-medium">Username</span>
          <input
            className="cockpit-input mt-1 w-full"
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
            className="cockpit-input mt-1 w-full"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="cockpit-button cockpit-button-primary w-full"
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
