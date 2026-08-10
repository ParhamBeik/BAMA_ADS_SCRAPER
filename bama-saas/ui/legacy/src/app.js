// App bootstrap: build the sidebar nav from the route table, wire the router,
// and reflect auth state in the user menu. Pages own their rendering.
import { ROUTES, match, navigate } from "./router.js";
import { isLoggedIn, cachedUser, logout } from "./auth.js";
import { $, el, clear, append } from "./ui.js";

const view = () => document.getElementById("view");

function isStaff() {
  return !!cachedUser()?.user?.is_staff;
}

function buildNav() {
  const nav = $("#nav");
  clear(nav);
  let lastGroup = null;
  for (const r of ROUTES) {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      nav.appendChild(el("div", { class: "nav-section", text: r.group }));
    }
    if (r.staff && !isStaff()) continue;
    if (!r.icon) continue; // detail routes have no icon → not in nav
    const a = el("a", { href: `#${r.pattern}`, "data-path": r.pattern }, [
      el("span", { class: "ico", text: r.icon }), document.createTextNode(r.label),
    ]);
    nav.appendChild(a);
  }
}

function setActive(path) {
  for (const a of document.querySelectorAll(".nav a")) {
    a.classList.toggle("active", a.getAttribute("data-path") === path);
  }
}

function renderUserMenu() {
  const host = $("#userMenu");
  clear(host);
  const u = cachedUser()?.user;
  if (isLoggedIn() && u) {
    host.appendChild(el("span", { class: "muted", text: u.email }));
    host.appendChild(el("button", { class: "btn sm ghost", text: "خروج", onclick: async () => { await logout(); location.hash = "#/login"; } }));
  } else {
    host.appendChild(el("a", { class: "btn sm", href: "#/login", text: "ورود" }));
  }
}

function onRoute() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const m = match(hash);
  const path = m?.route.pattern || "/";
  setActive(m?.route.icon ? path : ""); // only highlight nav routes
  document.getElementById("sidebar")?.classList.remove("open");
  if (m) {
    m.route.render(view(), m.params);
  } else {
    clear(view());
    view().appendChild(el("div", { class: "card" }, [
      el("h2", { text: "صفحه یافت نشد" }),
      el("a", { href: "#/", text: "بازگشت به داشبورد" }),
    ]));
  }
}

function refreshAuthChrome() {
  buildNav();
  renderUserMenu();
}

function boot() {
  buildNav();
  renderUserMenu();
  window.addEventListener("hashchange", onRoute);
  window.addEventListener("auth:logout", () => {
    refreshAuthChrome();
    if (!["/", "/login", "/register"].includes(location.hash.replace(/^#/, ""))) {
      navigate("/login");
    }
  });
  window.addEventListener("auth:login", refreshAuthChrome);

  $("#menuToggle")?.addEventListener("click", () => {
    document.getElementById("sidebar")?.classList.toggle("open");
  });

  // Dispatch auth:login when a token lands (so nav refreshes after login/register).
  const _setItem = localStorage.setItem.bind(localStorage);
  localStorage.setItem = (k, v) => {
    const had = localStorage.getItem(k);
    _setItem(k, v);
    if (k === "bama.jwt" && !had && v) window.dispatchEvent(new CustomEvent("auth:login"));
  };

  onRoute();
}

boot();
