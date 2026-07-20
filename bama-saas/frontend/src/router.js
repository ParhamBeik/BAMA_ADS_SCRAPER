// Hash router. The route table is the single source of truth for both routing
// and the sidebar nav (app.js reads ROUTES to build the nav). Patterns use
// `:param` segments compiled to capture groups.
import * as browse from "./pages/browse.js";
import * as analytics from "./pages/analytics.js";
import * as engagement from "./pages/engagement.js";
import * as account from "./pages/account.js";

export const ROUTES = [
  // Market (always visible)
  { pattern: "/",              label: "داشبورد",       icon: "🏠", group: "بازار", render: browse.dashboard },
  { pattern: "/search",        label: "جستجو",         icon: "🔍", group: "بازار", render: browse.search },
  { pattern: "/market",        label: "بازار خودرو",   icon: "🚗", group: "بازار", render: browse.market },
  { pattern: "/analytics",     label: "تحلیل بازار",   icon: "🧮", group: "بازار", render: analytics.analytics },
  { pattern: "/charts/price",  label: "نمودار قیمت",   icon: "📈", group: "بازار", render: analytics.priceCharts },
  { pattern: "/charts/inventory", label: "نمودار موجودی", icon: "📦", group: "بازار", render: analytics.inventoryCharts },

  // Engagement (premium, auth)
  { pattern: "/favorites",     label: "علاقه‌مندی‌ها",  icon: "⭐", group: "ابزار من", render: engagement.favorites },
  { pattern: "/watchlists",    label: "واچ‌لیست‌ها",    icon: "👁", group: "ابزار من", render: engagement.watchlists },
  { pattern: "/alerts",        label: "هشدارها",        icon: "🔔", group: "ابزار من", render: engagement.alerts },

  // Account
  { pattern: "/profile",       label: "پروفایل",       icon: "👤", group: "حساب", render: account.profile },
  { pattern: "/subscription",  label: "اشتراک",         icon: "💳", group: "حساب", render: account.subscription },
  { pattern: "/admin",         label: "مدیریت",         icon: "🛠", group: "حساب", render: account.admin, staff: true },

  // Detail routes (not in nav)
  { pattern: "/brand/:slug",   render: browse.brand },
  { pattern: "/model/:id",     render: browse.modelPage },
  { pattern: "/variant/:id",   render: browse.variant },
  { pattern: "/ad/:code",      render: browse.adDetail },
  { pattern: "/login",         render: account.loginPage },
  { pattern: "/register",      render: account.registerPage },
];

function compile(pattern) {
  const names = [];
  const re = new RegExp("^" + pattern.replace(/:([^/]+)/g, (_, n) => { names.push(n); return "([^/]+)"; }) + "$");
  return { re, names };
}

for (const r of ROUTES) {
  const { re, names } = compile(r.pattern);
  r._re = re; r._names = names;
}

export function match(path) {
  for (const r of ROUTES) {
    const m = r._re.exec(path);
    if (m) {
      const params = {};
      r._names.forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); });
      return { route: r, params };
    }
  }
  return null;
}

export function navigate(path) {
  location.hash = "#" + path;
}
