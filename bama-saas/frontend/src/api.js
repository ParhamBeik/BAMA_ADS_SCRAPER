// Thin fetch wrapper + endpoint helpers. Pages call these; no business logic here.
import { API_BASE, TOKEN_KEY } from "./config.js";
import { getToken, clearSession } from "./auth.js";

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path, params) {
  const url = new URL(`${API_BASE}${path}`, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) {
    if (v == null || v === "" || v === false) continue;
    url.searchParams.set(k, v);
  }
  // If API_BASE is absolute (dev), keep the absolute URL; otherwise the relative
  // path is enough and keeps cookies/origin clean in prod.
  return API_BASE ? url.toString() : `${path}${url.search}`;
}

async function request(method, path, { params, body } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body != null) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body != null ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError("ارتباط با سرور برقرار نشد", 0, e.message);
  }

  if (res.status === 401) {
    // Token missing/invalid/expired. Drop local session so the UI shows logged-out.
    clearSession();
    window.dispatchEvent(new CustomEvent("auth:logout"));
  }

  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `خطای ${res.status}`;
    throw new ApiError(msg, res.status, data);
  }
  return data;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body, params) => request("POST", path, { body, params }),
  patch: (path, body, params) => request("PATCH", path, { body, params }),
  del: (path, params) => request("DELETE", path, { params }),
};

/** Normalize both paginated ({count,next,results}) and bare-list responses. */
export function pageOf(data) {
  if (Array.isArray(data)) return { items: data, count: data.length, next: null };
  return { items: data.results || [], count: data.count ?? 0, next: data.next };
}

/** Read ?page from a `next` URL (PageNumberPagination). */
export function pageFromUrl(url) {
  if (!url) return null;
  return new URL(url, location.origin).searchParams.get("page");
}
