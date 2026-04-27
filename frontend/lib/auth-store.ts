import { create } from "zustand";

export type Role = "chat" | "code" | "admin";

export interface Me {
  id: number;
  username: string;
  role: Role;
  must_change_password: boolean;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  setMe: (me: Me | null) => void;
  setLoading: (loading: boolean) => void;
  reset: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  me: null,
  loading: true,
  setMe: (me) => set({ me, loading: false }),
  setLoading: (loading) => set({ loading }),
  reset: () => set({ me: null, loading: false }),
}));

export function hasAtLeast(role: Role, min: Role): boolean {
  const rank: Record<Role, number> = { chat: 0, code: 1, admin: 2 };
  return rank[role] >= rank[min];
}
