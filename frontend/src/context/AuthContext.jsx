import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, SESSION_ENDED_EVENT } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  // True when a signed-in tab found its session gone, so sign-in can say why.
  const [sessionEnded, setSessionEnded] = useState(false);
  const userRef = useRef(null);
  const recheckRef = useRef(null);

  useEffect(() => {
    userRef.current = user;
  }, [user]);

  const refresh = useCallback(async () => {
    try {
      const current = await api("/auth/me");
      setUser(current);
      setSessionEnded(false);
      return current;
    } catch (error) {
      if (error.status !== 401) {
        console.error("Unable to load session", error);
      }
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Any call answered 401 while this tab thinks it is signed in: ask the server
  // once, however many calls failed together. Only a 401 from /auth/me itself
  // signs the tab out; anything else (restart, network) leaves it as it is.
  useEffect(() => {
    const onSessionEnded = () => {
      if (!userRef.current || recheckRef.current) return;
      recheckRef.current = api("/auth/me")
        .then((current) => setUser(current))
        .catch((error) => {
          if (error.status === 401) {
            setSessionEnded(true);
            setUser(null);
          }
        })
        .finally(() => {
          recheckRef.current = null;
        });
    };
    window.addEventListener(SESSION_ENDED_EVENT, onSessionEnded);
    return () => window.removeEventListener(SESSION_ENDED_EVENT, onSessionEnded);
  }, []);

  const login = useCallback(async (email, password) => {
    const account = await api("/auth/login", {
      method: "POST",
      body: { email, password },
    });
    setUser(account);
    setSessionEnded(false);
    return account;
  }, []);

  const register = useCallback(async (name, email, password) => {
    const account = await api("/auth/register", {
      method: "POST",
      body: { name, email, password },
    });
    setUser(account);
    setSessionEnded(false);
    return account;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
      setSessionEnded(false);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, sessionEnded, login, register, logout, refresh }),
    [user, loading, sessionEnded, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
