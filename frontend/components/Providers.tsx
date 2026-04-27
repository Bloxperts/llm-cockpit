"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Me, useAuthStore } from "@/lib/auth-store";
import { applyTheme, getEffectiveTheme } from "@/lib/dark-mode";

function AuthBootstrap({ children }: { children: React.ReactNode }) {
  const { setMe, reset, setLoading } = useAuthStore();

  // Apply the effective theme on first paint so the page doesn't flash
  // light → dark on initial render. Runs once.
  useEffect(() => {
    applyTheme(getEffectiveTheme());
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const me = await api<Me>("/api/auth/me");
        if (!cancelled) setMe(me);
      } catch (e) {
        if (!cancelled) reset();
        if (e instanceof ApiError && e.status === 409) {
          // The /api/auth/me route is allowed for must_change_password=true
          // users (UC-09), so 409 here means the cookie is *gone* — fall
          // through to login.
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [setMe, reset, setLoading]);

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthBootstrap>{children}</AuthBootstrap>
    </QueryClientProvider>
  );
}
