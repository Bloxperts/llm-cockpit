"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { api } from "@/lib/api";
import { hasAtLeast, useAuthStore } from "@/lib/auth-store";

export function AppHeader() {
  const me = useAuthStore((s) => s.me);
  const reset = useAuthStore((s) => s.reset);
  const pathname = usePathname();

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

  const links: Array<{ href: string; label: string; show: boolean }> = [
    { href: "/dashboard/", label: "Dashboard", show: true },
    { href: "/chat/", label: "Chat", show: hasAtLeast(me.role, "chat") },
    { href: "/code/", label: "Code", show: hasAtLeast(me.role, "code") },
  ];

  return (
    <header className="border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
        <span className="font-semibold">LLM Cockpit</span>
        <nav className="flex gap-3 text-sm">
          {links
            .filter((l) => l.show)
            .map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={
                  pathname?.startsWith(l.href.replace(/\/$/, ""))
                    ? "font-semibold underline-offset-4 underline"
                    : "text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100"
                }
              >
                {l.label}
              </Link>
            ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          <span className="text-neutral-600 dark:text-neutral-400">
            {me.username} · {me.role}
          </span>
          <button
            type="button"
            onClick={logout}
            className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
