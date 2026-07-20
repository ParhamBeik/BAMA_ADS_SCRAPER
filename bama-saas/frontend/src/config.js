// Runtime config. No build step, so config is a plain module.
//
// API_BASE: empty string in production (same-origin: nginx serves the SPA and
// proxies /api/* to the Django backend). In dev the SPA is served by a static
// server on :8080 while Django runs on :8000, so we point at the backend host.
const DEV = location.port === "8080";

export const API_BASE = window.API_BASE ?? (DEV ? "http://127.0.0.1:8000" : "");
export const TOKEN_KEY = "bama.jwt";
export const USER_KEY = "bama.user";
export const PAGE_SIZE = 25;
