import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import {
  AuthError, login as loginRequest, logout as logoutRequest, refresh as refreshRequest,
  signup as signupRequest, type AuthSession, type AuthUser, type SignupPayload,
} from "@/lib/auth";
import { setAccessTokenGetter } from "@/lib/slack-connector";
import { setAccessTokenGetter as setEmailAccessTokenGetter } from "@/lib/email-connector";
import { setAccessTokenGetter as setInvoiceAccessTokenGetter } from "@/lib/invoice-service";
import { setAccessTokenGetter as setDocumentsAccessTokenGetter } from "@/lib/documents-service";
import { setAccessTokenGetter as setVendorsAccessTokenGetter } from "@/lib/vendors-service";
import { setAccessTokenGetter as setTransactionsAccessTokenGetter } from "@/lib/transactions-service";
import { setAccessTokenGetter as setHrAccessTokenGetter } from "@/lib/hr-service";
import { setAccessTokenGetter as setProcurementAccessTokenGetter } from "@/lib/procurement-service";
import { setAccessTokenGetter as setSettingsAccessTokenGetter } from "@/lib/settings-service";
import { setAccessTokenGetter as setReportsAccessTokenGetter } from "@/lib/reports-service";

interface AuthContextValue {
  user: AuthUser | null;
  /** null until the initial refresh attempt finishes — routes must wait, not redirect. */
  isRestoring: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (payload: SignupPayload) => Promise<void>;
  logout: () => Promise<void>;
  /** Access token for calling other services. Read at call time, never stored elsewhere. */
  getAccessToken: () => string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Refresh this many ms before expiry so a request never races the clock. */
const REFRESH_MARGIN_MS = 60_000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);
  // Deliberately a ref, not state: the token is not rendered, and keeping it out
  // of state avoids re-rendering the whole tree every time it rotates.
  const accessTokenRef = useRef<string | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hand the connector clients a way to read the current token. Registered once,
  // reading through the ref, so a rotated token is picked up automatically.
  useEffect(() => {
    setAccessTokenGetter(() => accessTokenRef.current);
    setEmailAccessTokenGetter(() => accessTokenRef.current);
    setInvoiceAccessTokenGetter(() => accessTokenRef.current);
    setDocumentsAccessTokenGetter(() => accessTokenRef.current);
    setVendorsAccessTokenGetter(() => accessTokenRef.current);
    setTransactionsAccessTokenGetter(() => accessTokenRef.current);
    setHrAccessTokenGetter(() => accessTokenRef.current);
    setProcurementAccessTokenGetter(() => accessTokenRef.current);
    setSettingsAccessTokenGetter(() => accessTokenRef.current);
    setReportsAccessTokenGetter(() => accessTokenRef.current);
  }, []);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const applySession = useCallback(
    (session: AuthSession) => {
      accessTokenRef.current = session.access_token;
      setUser(session.user);

      // Renew slightly early and keep the chain going, so a tab left open for
      // hours stays signed in rather than failing on the next click.
      clearRefreshTimer();
      const delay = Math.max(session.expires_in * 1000 - REFRESH_MARGIN_MS, 15_000);
      refreshTimerRef.current = setTimeout(() => {
        refreshRequest()
          .then(applySession)
          .catch(() => {
            accessTokenRef.current = null;
            setUser(null);
          });
      }, delay);
    },
    [clearRefreshTimer],
  );

  // On load the access token is gone (memory only), but the refresh cookie may
  // still be valid — so try once to restore the session before deciding the user
  // is logged out.
  useEffect(() => {
    let cancelled = false;
    refreshRequest()
      .then((session) => {
        if (!cancelled) applySession(session);
      })
      .catch(() => {
        /* no valid session — expected for a signed-out visitor */
      })
      .finally(() => {
        if (!cancelled) setIsRestoring(false);
      });
    return () => {
      cancelled = true;
      clearRefreshTimer();
    };
  }, [applySession, clearRefreshTimer]);

  const login = useCallback(
    async (email: string, password: string) => {
      applySession(await loginRequest(email, password));
    },
    [applySession],
  );

  const signup = useCallback(
    async (payload: SignupPayload) => {
      applySession(await signupRequest(payload));
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    clearRefreshTimer();
    try {
      await logoutRequest();
    } catch {
      // Revoking server-side is best effort; the local session must be dropped
      // either way, or "log out" would appear to fail while the service is down.
    }
    accessTokenRef.current = null;
    setUser(null);
  }, [clearRefreshTimer]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isRestoring,
      isAuthenticated: user !== null,
      login,
      signup,
      logout,
      getAccessToken: () => accessTokenRef.current,
    }),
    [user, isRestoring, login, signup, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

export { AuthError };
