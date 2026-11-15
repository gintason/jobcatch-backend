import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, tokenStore } from "./api";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_email_verified: boolean;
  is_identity_verified: boolean;
  profile: unknown;
}

export interface RegisterPayload {
  full_name: string;
  email: string;
  phone?: string;
  /** Optional: the platform is single-account; users pick a role at the hub. */
  role?: string;
  password: string;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Sign in (or sign up) with a Google ID token from Google Identity Services. */
  loginWithGoogle: (credential: string) => Promise<{ created: boolean }>;
  register: (payload: RegisterPayload) => Promise<{ user_id: string }>;
  verifyOtp: (email: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  switchRole: (role: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Hydrate the session on first load if we have a stored token.
  useEffect(() => {
    (async () => {
      if (tokenStore.access()) {
        try {
          const { data } = await api.get("/auth/me/");
          setUser(data);
        } catch {
          tokenStore.clear();
        }
      }
      setLoading(false);
    })();
  }, []);

  async function login(email: string, password: string) {
    const { data } = await api.post("/auth/login/", { email, password });
    tokenStore.set(data.access, data.refresh);
    setUser(data.user);
  }

  async function loginWithGoogle(credential: string) {
    const res = await api.post("/auth/google/", { credential });
    tokenStore.set(res.data.access, res.data.refresh);
    setUser(res.data.user);
    return { created: res.status === 201 };
  }

  async function register(payload: RegisterPayload) {
    const { data } = await api.post("/auth/register/", payload);
    return data as { user_id: string };
  }

  async function verifyOtp(email: string, code: string) {
    const { data } = await api.post("/auth/verify-otp/", {
      email,
      code,
      purpose: "email_verify",
    });
    tokenStore.set(data.access, data.refresh);
    setUser(data.user);
  }

  /**
   * Set which role the user is currently acting as. The backend updates the
   * account and permissions read it from the DB, so it applies immediately.
   */
  async function switchRole(role: string) {
    const { data } = await api.post("/auth/switch-role/", { role });
    setUser((prev) => (prev ? { ...prev, ...data } : prev));
  }

  async function logout() {
    try {
      await api.post("/auth/logout/", { refresh: tokenStore.refresh() });
    } catch {
      /* ignore — clear locally regardless */
    }
    tokenStore.clear();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, loginWithGoogle, register, verifyOtp, logout, switchRole }}>
      {children}
    </AuthContext.Provider>
  );
}
