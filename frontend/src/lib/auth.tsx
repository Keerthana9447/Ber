import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, refreshSession, setUnauthorizedHandler, tokenStore, type TokenResponse } from "./api";

export type User = { id: number; email: string; full_name: string; role: string; permissions: string[] };
type Status = "loading" | "authed" | "anon";

type AuthContextValue = {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission?: string) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);

  const endSession = useCallback(() => {
    tokenStore.clear();
    queryClient.clear();
    setUser(null);
    setStatus("anon");
  }, [queryClient]);

  useEffect(() => {
    setUnauthorizedHandler(endSession);
    return () => setUnauthorizedHandler(null);
  }, [endSession]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!tokenStore.getRefresh() || !(await refreshSession())) {
        if (!cancelled) setStatus("anon");
        return;
      }
      try {
        const me = await api<User>("/auth/me");
        if (!cancelled) {
          setUser(me);
          setStatus("authed");
        }
      } catch {
        if (!cancelled) endSession();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [endSession]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api<TokenResponse>("/auth/login", { method: "POST", body: { email, password } });
    tokenStore.setAccess(tokens.access_token);
    tokenStore.setRefresh(tokens.refresh_token);
    const me = await api<User>("/auth/me");
    setUser(me);
    setStatus("authed");
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = tokenStore.getRefresh();
    try {
      if (refreshToken) await api("/auth/logout", { method: "POST", body: { refresh_token: refreshToken } });
    } catch {
      /* the local session is cleared regardless */
    }
    endSession();
  }, [endSession]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      login,
      logout,
      can: (permission) => !permission || (user?.permissions.includes(permission) ?? false),
    }),
    [status, user, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
