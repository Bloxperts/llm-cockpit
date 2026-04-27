"use client";

import { useEffect } from "react";

import { useAuthStore } from "@/lib/auth-store";

export default function HomePage() {
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
    window.location.replace("/dashboard/");
  }, [me, loading]);

  return (
    <main className="flex-1 flex items-center justify-center text-neutral-500">
      Loading…
    </main>
  );
}
