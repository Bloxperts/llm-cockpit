"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { hasAtLeast, useAuthStore } from "@/lib/auth-store";
import { Theme, getEffectiveTheme, toggleTheme } from "@/lib/dark-mode";

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
  const [theme, setThemeState] = useState<Theme>(() => getEffectiveTheme());
  const [menuOpen, setMenuOpen] = useState(false);
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    api<{ version: string }>("/api/version")
      .then((payload) => {
        if (active) setAppVersion(payload.version);
      })
      .catch(() => {
        if (active) setAppVersion(null);
      });
    return () => {
      active = false;
    };
  }, []);

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
    } catch {}
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
    { href: "/admin/users/", label: "Users", show: me.role === "admin" },
    { href: "/admin/ollama/", label: "Ollama", show: me.role === "admin" },
  ];

  return (
    <header className="cockpit-header sticky top-0 z-40 border-b border-[var(--cockpit-border)] bg-[color-mix(in_srgb,var(--cockpit-surface)_88%,transparent)] backdrop-blur-lg">
      <div className="w-full max-w-[1300px] mx-auto px-3 sm:px-4 min-h-16 flex items-center gap-3">
        <Link href="/dashboard/" className="flex items-center gap-2 font-semibold tracking-tight text-neutral-900 dark:text-neutral-50">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-neutral-950 text-white dark:bg-white dark:text-neutral-950 font-mono text-sm shadow-sm">
            LC
          </span>
          <span className="hidden sm:inline">LLM Cockpit</span>
          {appVersion ? (
            <span className="hidden rounded-md border border-[var(--cockpit-border)] px-1.5 py-0.5 font-mono text-[10px] font-medium text-neutral-500 dark:text-neutral-400 md:inline">
              v{appVersion}
            </span>
          ) : null}
        </Link>

        <nav className="flex flex-wrap items-center gap-1.5 text-sm ml-1 sm:ml-3">
          {links.filter((l) => l.show).map((l) => {
            const active = pathname?.startsWith(l.href.replace(/\/$/, ""));
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-lg px-3 py-1.5 font-medium border ${
                  active
                    ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-950"
                    : "border-transparent text-neutral-600 dark:text-neutral-300 hover:border-[var(--cockpit-border)] hover:bg-[var(--cockpit-surface-muted)]"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2 text-sm">
          <button type="button" onClick={onThemeToggle} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} className="rounded-lg border border-transparent p-2 text-neutral-600 dark:text-neutral-300 hover:border-[var(--cockpit-border)] hover:bg-[var(--cockpit-surface-muted)]">
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>

          <div className="relative" ref={menuRef}>
            <button type="button" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)} className="rounded-lg border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] px-3 py-1.5 text-xs text-neutral-700 dark:text-neutral-200 hover:bg-[var(--cockpit-surface-muted)] shadow-sm">
              {me.username} · {me.role} ▾
            </button>
            {menuOpen ? (
              <div role="menu" className="absolute right-0 mt-2 w-64 rounded-xl border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] shadow-lg z-50 p-2 text-xs">
                <Link href="/change-password/" onClick={() => setMenuOpen(false)} className="block rounded-lg px-3 py-2 text-neutral-700 dark:text-neutral-200 hover:bg-[var(--cockpit-surface-muted)]" role="menuitem">
                  Change password
                </Link>
                <div className="mt-1 border-t border-[var(--cockpit-border)] px-3 py-2">
                  <label className="block">
                    <span className="mb-1 block text-[10px] uppercase tracking-wide text-neutral-500 dark:text-neutral-400">Session duration</span>
                    <select value={String(me.session_ttl_days ?? 7)} onChange={(e) => void changeSessionTtl(Number(e.target.value))} className="cockpit-input w-full py-1 text-xs">
                      {TTL_OPTIONS.map((opt) => (
                        <option key={opt.days} value={String(opt.days)}>{opt.label}</option>
                      ))}
                    </select>
                    <span className="mt-1 block text-[10px] text-neutral-500">Takes effect on next login.</span>
                  </label>
                </div>
                <button type="button" onClick={() => { setMenuOpen(false); void logout(); }} className="mt-1 w-full rounded-lg border-t border-[var(--cockpit-border)] px-3 py-2 text-left text-neutral-700 dark:text-neutral-200 hover:bg-[var(--cockpit-surface-muted)]" role="menuitem">
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
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
