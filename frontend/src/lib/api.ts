// Thin fetch wrapper for /api/v1. Access token lives in memory only; the refresh token is
// kept in sessionStorage (cleared when the tab closes). On a 401 it refreshes once, then retries.
const API_PREFIX = "/api/v1";
const REFRESH_KEY = "berev.rt";

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_at: string;
};

export const tokenStore = {
  setAccess(token: string | null) {
    accessToken = token;
  },
  getRefresh(): string | null {
    try {
      return sessionStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  setRefresh(token: string | null) {
    try {
      if (token) sessionStorage.setItem(REFRESH_KEY, token);
      else sessionStorage.removeItem(REFRESH_KEY);
    } catch {
      /* storage unavailable: session simply will not survive a reload */
    }
  },
  clear() {
    accessToken = null;
    tokenStore.setRefresh(null);
  },
};

export class ApiError extends Error {
  status: number;
  detail?: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

/** True when the backend has no such route yet (FastAPI answers 404/405 for unknown routes). */
export const isEndpointMissing = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 404 || error.status === 405);

export function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item),
      )
      .join("; ");
  }
  return fallback;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return `${API_PREFIX}${path}${qs ? `?${qs}` : ""}`;
}

let refreshing: Promise<boolean> | null = null;

/** Single-flight refresh: concurrent callers share one request (refresh tokens rotate). */
export function refreshSession(): Promise<boolean> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const refreshToken = tokenStore.getRefresh();
    if (!refreshToken) return false;
    try {
      const res = await fetch(buildUrl("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        tokenStore.clear();
        return false;
      }
      const data = (await res.json()) as TokenResponse;
      tokenStore.setAccess(data.access_token);
      tokenStore.setRefresh(data.refresh_token);
      return true;
    } catch {
      return false; // network problem: keep the token so a later attempt can succeed
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

type Options = { method?: string; body?: unknown; query?: Query; signal?: AbortSignal };

export async function api<T = unknown>(path: string, options: Options = {}, allowRetry = true): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server. Check that the backend is running.");
  }

  if (res.status === 401 && allowRetry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, options, false);
    onUnauthorized?.();
  }
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : undefined;
  } catch {
    payload = text;
  }
  if (!res.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload ? (payload as { detail: unknown }).detail : payload;
    throw new ApiError(res.status, messageFromDetail(detail, `Request failed (${res.status})`), detail);
  }
  return payload as T;
}
