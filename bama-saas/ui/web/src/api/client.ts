/**
 * Cookie-session API client. Credentials are HTTP-only cookies; CSRF is
 * sent for unsafe methods. Bearer localStorage remains as a legacy fallback
 * for tests that still inject Authorization headers.
 */
import type { paths } from "./schema";

export type ApiPath = keyof paths;

const BASE = import.meta.env.VITE_API_BASE ?? "";

function csrfToken(): string | null {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;

  constructor(status: number, detail: string, body?: unknown) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.body = body;
  }

  get isSubscriptionRequired() {
    return this.status === 403 && /subscription|plan|feature/i.test(this.detail);
  }

  get isAuthRequired() {
    return this.status === 401;
  }

  get isVerificationRequired() {
    return this.status === 403 && /verif/i.test(this.detail);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRFToken", csrf);
  }

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json().catch(() => ({}))
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof body === "object" && body && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed (${response.status})`;
    throw new ApiError(response.status, detail, body);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export interface Envelope {
  as_of: string;
  methodology_version: number;
  coverage: {
    complete_sweep: boolean;
    swept_at?: string;
    ads_covered?: number;
    deepest_rank?: number;
    stale?: boolean;
    age_hours?: number;
    reason?: string;
  };
  available?: boolean;
  reason?: string;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface MeResponse {
  user: {
    id: string;
    email: string;
    full_name: string;
    is_staff: boolean;
    email_verified_at: string | null;
  };
  subscription: { plan_type: string; status: string; expires_at: string | null } | null;
  plan: string;
  limits: Record<string, number | boolean>;
  verified: boolean;
}
