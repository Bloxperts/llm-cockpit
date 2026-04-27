"use client";

import { useEffect } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ChatShell } from "@/components/ChatShell";
import { useAuthStore } from "@/lib/auth-store";

export default function ChatPage() {
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
  }, [me, loading]);

  if (loading || !me) {
    return (
      <main className="flex-1 flex items-center justify-center text-neutral-500">
        Loading…
      </main>
    );
  }

  return (
    <>
      <AppHeader />
      <ChatShell mode="chat" />
    </>
  );
}
