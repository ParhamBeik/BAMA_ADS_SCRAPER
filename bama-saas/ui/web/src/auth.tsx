import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type MeResponse, ApiError } from "./api/client";

type AuthState = {
  me: MeResponse | null;
  loading: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<MeResponse>;
  register: (email: string, password: string, full_name: string) => Promise<MeResponse>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<MeResponse>("/api/auth/me/");
      setMe(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await api.post<MeResponse>("/api/auth/login/", { email, password });
    setMe(data);
    return data;
  }, []);

  const register = useCallback(async (email: string, password: string, full_name: string) => {
    const data = await api.post<MeResponse>("/api/auth/register/", { email, password, full_name });
    setMe(data);
    return data;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout/");
    } finally {
      setMe(null);
    }
  }, []);

  const value = useMemo(
    () => ({ me, loading, refresh, login, register, logout }),
    [me, loading, refresh, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth requires AuthProvider");
  return ctx;
}
