"use client";

import { useEffect } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ChatShell } from "@/components/ChatShell";
import { hasAtLeast, useAuthStore } from "@/lib/auth-store";

export default function CodePage() {
  const { me, loading } = useAuthStore();

  useEffect(() => {
    if (loading) return;
    if (!me) {
      window.location.replace("/login/");
      return;
    }
    if (me.must_change_password) {
      window.location.replace("/change-password/");
      return;
    }
    if (!hasAtLeast(me.role, "code")) {
      window.location.replace("/chat/");
    }
  }, [me, loading]);

  if (loading || !me || !hasAtLeast(me.role, "code")) {
    return (
      <main className="flex-1 flex items-center justify-center text-neutral-500">
        Loading…
      </main>
    );
  }

  return (
    <>
      <AppHeader />
      <ChatShell mode="code" />
    </>
  );
}
