/**
 * The one place that talks to the backend.
 *
 * Paths are checked against `schema.d.ts`, which is generated from the Django
 * OpenAPI schema (`npm run api:types`). That turns a renamed or removed endpoint
 * into a compile error instead of a blank panel someone notices in production —
 * the class of bug that a hand-written fetch wrapper cannot catch at all.
 */
import type { paths } from "./schema";

export type ApiPath = keyof paths;

const BASE = import.meta.env.VITE_API_BASE ?? "";

const ACCESS_KEY = "bama.access";
const REFRESH_KEY = "bama.refresh";

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh?: string) {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }

  /** 403 on a research endpoint means "needs a subscription", not "broken". */
  get isSubscriptionRequired() {
    return this.status === 403 && /subscription/i.test(this.detail);
  }

  get isAuthRequired() {
    return this.status === 401;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (tokens.access) headers.set("Authorization", `Bearer ${tokens.access}`);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail =
      (body as { detail?: string }).detail ?? `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** Provenance every research answer carries. Rendered, never silently dropped. */
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
