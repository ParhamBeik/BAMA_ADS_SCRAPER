// Shared DOM helpers, formatters, and tiny UI components. No framework.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Create an element with props/children in one call. */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    parent.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---------- formatters ----------
const NF = new Intl.NumberFormat("en-US");

/** Iranian Toman price → compact Persian-ish ("۸.۴۰ میلیارد"). */
export function price(n) {
  if (n == null || n === "" || isNaN(n)) return "—";
  n = Number(n);
  if (n >= 1e9) return `${(n / 1e9).toLocaleString("fa-IR", { maximumFractionDigits: 2 })} میلیارد`;
  if (n >= 1e6) return `${(n / 1e6).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} میلیون`;
  return n.toLocaleString("fa-IR");
}

export function num(n) {
  if (n == null || n === "" || isNaN(n)) return "—";
  return Number(n).toLocaleString("fa-IR");
}

export function pct(n, digits = 1) {
  if (n == null || isNaN(n)) return "—";
  return `${Number(n).toLocaleString("fa-IR", { maximumFractionDigits: digits })}٪`;
}

export function date(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleDateString("fa-IR", { year: "numeric", month: "short", day: "numeric" });
}

export function datetime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("fa-IR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(iso) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (isNaN(s)) return "—";
  const rtf = new Intl.RelativeTimeFormat("fa-IR", { numeric: "auto" });
  const units = [["سال", 31536000], ["روز", 86400], ["ساعت", 3600], ["دقیقه", 60]];
  for (const [u, sec] of units) {
    if (Math.abs(s) >= sec || u === "دقیقه") return rtf.format(-Math.round(s / sec), u);
  }
  return "اکنون";
}

export function miles(n) {
  if (n == null || isNaN(n)) return "—";
  return `${num(n)} کیلومتر`;
}

// ---------- components ----------
export function spinner(wrap = true) {
  const s = el("div", { class: "spinner" });
  return wrap ? el("div", { class: "spinner-wrap" }, [s]) : s;
}

export function card(title, children, extra = {}) {
  const c = el("div", { class: `card ${extra.class || ""}`.trim() });
  if (title) c.appendChild(el(title.h ? "h2" : "h2", { text: title }));
  append(c, [].concat(children));
  return c;
}

export function stat(label, value, sub, deltaKind) {
  return el("div", { class: "card stat" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value mono", text: value }),
    sub != null ? el("div", { class: `sub ${deltaKind || ""}`.trim(), text: sub }) : null,
  ]);
}

export function badge(text, kind = "gray") {
  return el("span", { class: `badge ${kind}`, text });
}

export function table(headers, rows, { renderRow } = {}) {
  const wrap = el("div", { class: "tbl-wrap" });
  const t = el("table");
  t.appendChild(el("thead", {}, [el("tr", {}, headers.map(h => el("th", { text: h })))]));
  const tbody = el("tbody");
  if (!rows || rows.length === 0) {
    tbody.appendChild(el("tr", {}, [el("td", { colspan: headers.length, class: "empty", text: "موردی یافت نشد" })]));
  } else {
    for (const r of rows) tbody.appendChild(renderRow ? renderRow(r) : el("tr", {}, r.map(c => el("td", { html: c == null ? "" : c }))));
  }
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

export function empty(text = "موردی یافت نشد") {
  return el("div", { class: "empty", text });
}

export function errorBox(msg) {
  return el("div", { class: "error-box", text: String(msg) });
}

/** Show a transient toast. */
let toastTimer;
export function toast(message, kind = "success") {
  const node = document.getElementById("toast");
  if (!node) return;
  node.textContent = message;
  node.className = `toast ${kind}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 3000);
}

/** Standard page runner: spinner while the async task runs, error box on throw. */
export async function runPage(view, task) {
  clear(view);
  view.appendChild(spinner());
  try {
    await task();
  } catch (e) {
    clear(view);
    view.appendChild(errorBox(e.status === 401 ? "برای ادامه وارد شوید" : e.message));
  }
}

/** Standard ad row used in many tables. */
export function adRow(ad, opts = {}) {
  const tr = el("tr");
  tr.appendChild(el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${ad.code}` }, [document.createTextNode(ad.title || ad.code)])]));
  if (opts.model !== false) tr.appendChild(el("td", { text: [ad.brand_name, ad.model_name].filter(Boolean).join(" ") }));
  tr.appendChild(el("td", { class: "mono", text: ad.year }));
  if (opts.mileage !== false) tr.appendChild(el("td", { class: "mono", text: miles(ad.mileage) }));
  tr.appendChild(el("td", { class: "mono", text: price(ad.current_price) }));
  tr.appendChild(el("td", { class: "muted", text: ago(ad.publish_at) }));
  return tr;
}
