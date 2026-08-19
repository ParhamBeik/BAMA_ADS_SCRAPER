/**
 * Session auth for the SPA.
 *
 * There is no token to store: the browser holds the Django sessionid cookie,
 * and this context just tracks whether GET /api/auth/me/ currently says
 * "logged in". That same call also bootstraps the CSRF cookie (see
 * MeView in apps/accounts/views_auth.py), so it has to run once before any
 * POST/PATCH/DELETE can succeed — App renders nothing else until it resolves.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "./api/client";

export interface AuthUser {
  email: string;
  is_staff: boolean;
  is_demo: boolean;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .get<AuthUser>("/api/auth/me/")
      .then((u) => !cancelled && setUser(u))
      .catch(() => !cancelled && setUser(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const u = await api.post<AuthUser>("/api/auth/login/", { email, password });
      setUser(u);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        throw new Error("Invalid email or password.");
      }
      if (err instanceof ApiError && err.status === 429) {
        throw new Error("Too many attempts. Wait a minute and try again.");
      }
      throw new Error("Login failed.");
    }
  }, []);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout/").catch(() => {});
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider.");
  return ctx;
}
