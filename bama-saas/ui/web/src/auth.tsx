/**
 * Session auth for the SPA.
 *
 * There is no token to store: the browser holds an HttpOnly sessionid cookie
 * that script cannot read, and this context just tracks whether
 * GET /api/auth/me/ currently says "logged in". That same call also bootstraps
 * the CSRF cookie (see MeView in apps/accounts/views.py), so it has to run once
 * before any POST/PATCH/DELETE can succeed — App renders nothing else until it
 * resolves.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";

export interface AuthUser {
  email: string;
  is_staff: boolean;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
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
      .then((u) => {
        if (cancelled) return;
        setUser(u);
      })
      .catch(() => {
        if (cancelled) return;
        setUser(null);
      })
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
        throw new Error("ایمیل یا گذرواژه نادرست است.");
      }
      if (err instanceof ApiError && err.status === 429) {
        throw new Error("تلاش بیش از حد. یک دقیقه صبر کنید و دوباره تلاش کنید.");
      }
      throw new Error("ورود ناموفق بود.");
    }
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    try {
      const u = await api.post<AuthUser>("/api/auth/register/", { email, password });
      setUser(u);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        const body = err.body as { email?: string[]; password?: string[] } | undefined;
        throw new Error(body?.email?.[0] ?? body?.password?.[0] ?? "لطفاً اطلاعات واردشده را بررسی کنید.");
      }
      if (err instanceof ApiError && err.status === 429) {
        throw new Error("تلاش بیش از حد برای ثبت‌نام. یک دقیقه صبر کنید و دوباره تلاش کنید.");
      }
      throw new Error("ساخت حساب ناموفق بود.");
    }
  }, []);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout/").catch(() => {});
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider.");
  return ctx;
}
