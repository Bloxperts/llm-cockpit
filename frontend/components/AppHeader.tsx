"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { hasAtLeast, useAuthStore } from "@/lib/auth-store";
import { getEffectiveTheme, toggleTheme } from "@/lib/dark-mode";

// Sprint 7 — JWT lifetime preferences. Mirrors the backend's TTL_MAP.
const TTL_OPTIONS: Array<{ days: number; label: string }> = [
  { days: 1, label: "1 day" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 0, label: "Unlimited" },
];

export function AppHeader() {
  const me = useAuthStore((s) => s.me);
  const setMe = useAuthStore((s) => s.setMe);
  const reset = useAuthStore((s) => s.reset);
  const pathname = usePathname();
  const [theme, setThemeState] = useState<"light" | "dark">("light");
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setThemeState(getEffectiveTheme());
  }, []);

  // Close the dropdown on outside-click / Escape.
  useEffect(() => {
    if (!menuOpen) return;
    function onPointer(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

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

  async function changeSessionTtl(days: number) {
    try {
      await api("/api/auth/session-ttl", {
        method: "PATCH",
        body: JSON.stringify({ ttl_days: days }),
      });
      // Reflect in the auth store so the dropdown shows the new value.
      // The backend persisted it; me.session_ttl_days is the source of truth.
      if (me) setMe({ ...me, session_ttl_days: days });
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Failed to update session duration: ${e.status}`);
      }
    }
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
          {/* Sprint 7 — user menu: change password + session TTL preference. */}
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
              className="text-xs rounded-md border border-neutral-200 dark:border-neutral-700 px-3 py-1 hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-200"
            >
              {me.username} · {me.role} ▾
            </button>
            {menuOpen ? (
              <div
                role="menu"
                className="absolute right-0 mt-2 w-64 rounded-lg border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 shadow-lg z-50 p-2 text-xs"
              >
                <Link
                  href="/change-password/"
                  onClick={() => setMenuOpen(false)}
                  className="block px-3 py-2 rounded-md hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-200"
                  role="menuitem"
                >
                  Change password
                </Link>
                <div className="px-3 py-2 border-t border-neutral-200 dark:border-neutral-800 mt-1">
                  <label className="block">
                    <span className="block text-[10px] uppercase tracking-wide text-neutral-500 dark:text-neutral-400 mb-1">
                      Session duration
                    </span>
                    <select
                      value={String(me.session_ttl_days ?? 7)}
                      onChange={(e) => void changeSessionTtl(Number(e.target.value))}
                      className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-2 py-1"
                    >
                      {TTL_OPTIONS.map((opt) => (
                        <option key={opt.days} value={String(opt.days)}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                    <span className="block mt-1 text-[10px] text-neutral-500 dark:text-neutral-500">
                      Takes effect on next login.
                    </span>
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    void logout();
                  }}
                  className="w-full text-left px-3 py-2 rounded-md hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-200 mt-1 border-t border-neutral-200 dark:border-neutral-800"
                  role="menuitem"
                >
                  Log out
                </button>
              </div>
            ) : null}
          </div>
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
