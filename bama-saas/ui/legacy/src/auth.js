// JWT session management. Tokens live in localStorage; /me is fetched lazily.
import { api } from "./api.js";
import { el, clear } from "./ui.js";
import { TOKEN_KEY, USER_KEY } from "./config.js";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setSession({ access, refresh }, user = null) {
  // simplejwt returns {access, refresh}; we only need access for Bearer auth.
  localStorage.setItem(TOKEN_KEY, access);
  if (refresh) localStorage.setItem(`${TOKEN_KEY}.refresh`, refresh);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(`${TOKEN_KEY}.refresh`);
  localStorage.removeItem(USER_KEY);
}

export function isLoggedIn() {
  return !!getToken();
}

export function cachedUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY));
  } catch {
    return null;
  }
}

export async function login(email, password) {
  const tok = await api.post("/api/auth/login/", { email, password });
  setSession(tok);
  const me = await fetchMe();
  return me;
}

export async function register({ email, password, full_name }) {
  await api.post("/api/auth/register/", { email, password, full_name });
  return login(email, password);
}

export async function fetchMe() {
  if (!isLoggedIn()) return null;
  try {
    const data = await api.get("/api/auth/me/");
    if (data?.user) localStorage.setItem(USER_KEY, JSON.stringify(data));
    return data;
  } catch {
    return cachedUser();
  }
}

export async function logout() {
  clearSession();
  window.dispatchEvent(new CustomEvent("auth:logout"));
}

/** Gate a view behind auth. Renders a login prompt and returns false if logged out. */
export function requireLogin(view) {
  if (isLoggedIn()) return true;
  clear(view);
  view.appendChild(el("div", { class: "card form-card" }, [
    el("h2", { text: "ورود لازم است" }),
    el("p", { class: "muted", text: "برای دسترسی به این بخش ابتدا وارد شوید." }),
    el("a", { class: "btn", href: "#/login", text: "ورود / ثبت‌نام" }),
  ]));
  return false;
}
