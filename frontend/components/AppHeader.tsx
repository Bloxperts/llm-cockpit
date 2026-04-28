"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { hasAtLeast, useAuthStore } from "@/lib/auth-store";
import { getEffectiveTheme, toggleTheme } from "@/lib/dark-mode";

export function AppHeader() {
  const me = useAuthStore((s) => s.me);
  const reset = useAuthStore((s) => s.reset);
  const pathname = usePathname();
  const [theme, setThemeState] = useState<"light" | "dark">("light");

  useEffect(() => {
    setThemeState(getEffectiveTheme());
  }, []);

  if (!me) return null;

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      // Ignore — we're going to /login regardless.
    }
    reset();
    window.location.replace("/login/");
  }

  function onThemeToggle() {
    const next = toggleTheme();
    setThemeState(next);
  }

  const links: Array<{ href: string; label: string; show: boolean }> = [
    { href: "/dashboard/", label: "Dashboard", show: true },
    { href: "/chat/", label: "Chat", show: hasAtLeast(me.role, "chat") },
    { href: "/code/", label: "Code", show: hasAtLeast(me.role, "code") },
    { href: "/admin/users/", label: "Users", show: me.role === "admin" },
  ];

  return (
    <header className="h-14 flex items-center border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900">
      <div className="max-w-7xl w-full mx-auto px-4 flex items-center gap-6">
        <span className="font-semibold text-neutral-900 dark:text-white tracking-tight">
          LLM Cockpit
        </span>
        <nav className="flex gap-1 text-sm">
          {links
            .filter((l) => l.show)
            .map((l) => {
              const active = pathname?.startsWith(l.href.replace(/\/$/, ""));
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  className={`px-3 py-1.5 rounded-full transition ${
                    active
                      ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                      : "text-neutral-600 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
        </nav>
        <div className="ml-auto flex items-center gap-2 text-sm">
          <button
            type="button"
            onClick={onThemeToggle}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="rounded-full p-2 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-600 dark:text-neutral-300"
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
          <span className="text-neutral-500 dark:text-neutral-400 text-xs hidden sm:inline">
            {me.username} · {me.role}
          </span>
          <button
            type="button"
            onClick={logout}
            className="rounded-md border border-neutral-200 dark:border-neutral-700 px-3 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-200"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
