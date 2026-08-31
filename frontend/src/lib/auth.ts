/**
 * Auth Service client.
 *
 * Token handling follows architecture report §19: the access token lives in
 * memory only, and the refresh token is an httpOnly cookie the browser sends
 * automatically. Nothing is written to localStorage — a token in localStorage is
 * readable by any script on the page, which turns one XSS bug into a stolen
 * session that outlives the tab.
 *
 * Because the refresh token is a cookie, every call here needs
 * `credentials: "include"`.
 */
const BASE = "/api/v1/auth";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  company_id: string;
  company_name: string;
  created_at: string;
}

export interface AuthSession {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface SignupPayload {
  company_name: string;
  full_name: string;
  email: string;
  password: string;
}

/** Carries the HTTP status so callers can distinguish 401 from 409 from 429. */
export class AuthError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "AuthError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch only rejects for network-level failures, which here almost always
    // means the Auth Service is not running.
    throw new AuthError("Could not reach the Auth Service. Is it running on port 8001?", 0);
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => ({}) as Record<string, unknown>);
  if (!response.ok) {
    const detail =
      typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new AuthError(detail, response.status);
  }
  return body as T;
}

export function signup(payload: SignupPayload): Promise<AuthSession> {
  return request<AuthSession>("/signup", { method: "POST", body: JSON.stringify(payload) });
}

export function login(email: string, password: string): Promise<AuthSession> {
  return request<AuthSession>("/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

/** Exchanges the refresh cookie for a new access token. Used on app start and on 401. */
export function refresh(): Promise<AuthSession> {
  return request<AuthSession>("/refresh", { method: "POST" });
}

export function logout(): Promise<void> {
  return request<void>("/logout", { method: "POST" });
}

export function me(accessToken: string): Promise<AuthUser> {
  return request<AuthUser>("/me", { headers: { Authorization: `Bearer ${accessToken}` } });
}
